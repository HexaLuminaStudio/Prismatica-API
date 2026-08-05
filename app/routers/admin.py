"""/v1/admin/* 路由(2026-08-05 M2 全面重写):

- grant / issue-codes (原有)
- users           (B2:列表 / 详情 / revoke-sessions / tier 修改)
- audit           (B2:查询)
- audit-summary   (B2:看板聚合)
- codes/lookup    (B2:查某码状态)
- metrics-summary (B2:全局 KPI)

权限:统一 requireAdminCookie(cookie 优先,X-Admin-Token 兜底)
actor:从 g.adminActor / g.adminUsername 拿,写入 audit_logs.actor
"""

from __future__ import annotations

import hashlib
import secrets
import string
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

from flask import Blueprint, g, jsonify, request
from loguru import logger
from pydantic import ValidationError
from sqlalchemy import func as saFunc
from sqlalchemy import select

from app.deps import getClientIp, requireAdmin, requireAdminCookie
from app.errors import ApiError
from app.db import getDb
from app.models import (
    AuditLog,
    Bill,
    LicenseCodeSeen,
    RechargeRecord,
    RefreshToken,
    UserAccount,
    UserBalance,
    UserDevice,
)
from app.models.license_models import InviteCode, RechargeCode, TrialCode, UserTier
from app.schemas.admin import (
    AdminGrantRequest,
    AdminGrantResponse,
    AdminIssueCodesRequest,
    AdminIssueCodesResponse,
    AdminUpdateUserTierRequest,
)
from app.schemas.admin_users import (
    AdminAuditItem,
    AdminAuditResponse,
    AdminAuditSummaryItem,
    AdminAuditSummaryResponse,
    AdminMetricsSummary,
    AdminUserDetail,
    AdminUserListItem,
    AdminUserListResponse,
    CodeLookupResponse,
    RevokeSessionsRequest,
    RevokeSessionsResponse,
)
from app.security import hmac as hmacUtil

bp = Blueprint("admin", __name__, url_prefix="/v1/admin")


@contextmanager
def _sessionCtx():
    with getDb() as db:
        yield db


def _actor() -> str:
    """拿当前管理员标识(供 audit_logs.actor)。"""
    return getattr(g, "adminUsername", None) or getattr(g, "adminActor", "admin")


def _audit(
    action: str,
    targetUser: str | None = None,
    details: dict | None = None,
) -> None:
    """写审计日志(独立事务,失败不影响主流程)。"""
    try:
        with getDb() as db:
            db.add(
                AuditLog(
                    actor=_actor(),
                    action=action,
                    targetUser=targetUser,
                    details=details,
                    ip=getClientIp(),
                )
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[Admin] audit 失败: {e}")


def _genCodeBody(prefix: str) -> str:
    """形如 INV-AB12-CD34-EF56-GH78 的码体(排除 0/O/1/I/L)。"""
    alphabet = "".join(
        c for c in (string.ascii_uppercase + string.digits) if c not in "0O1IL"
    )
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return f"{prefix}-" + "-".join(parts)


# ===========================================================================
# 原有接口(grant / issue-codes)
# ===========================================================================


@bp.post("/grant")
@requireAdmin
def postGrant():
    """运营手动给用户加余额(原接口保留,使用 X-Admin-Token 也可)。"""
    try:
        payload = AdminGrantRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    with _sessionCtx() as db:
        user = db.get(UserAccount, payload.userId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        balance = db.get(UserBalance, payload.userId)
        if balance is None:
            balance = UserBalance(userId=payload.userId)
            db.add(balance)
            db.flush()

        beforeBalance = balance.balance
        balance.balance += payload.amount
        balance.totalRecharged += payload.amount
        balance.version += 1
        afterBalance = balance.balance

        db.add(
            RechargeRecord(
                recordId=str(uuid.uuid4()),
                userId=payload.userId,
                amount=payload.amount,
                source="admin_grant",
                operatorNote=payload.note,
                balanceBefore=beforeBalance,
                balanceAfter=afterBalance,
            )
        )
        db.commit()

    _audit(
        action="admin.grant",
        targetUser=payload.userId,
        details={"amount": payload.amount, "note": payload.note},
    )
    logger.info(
        f"[Admin] grant user={payload.userId} +{payload.amount} balance={afterBalance}"
    )
    return jsonify(AdminGrantResponse(userId=payload.userId, newBalance=afterBalance).model_dump())


@bp.post("/issue-codes")
@requireAdmin
def postIssueCodes():
    """批量生成凭证(INV/TRY/RCH)。"""
    try:
        payload = AdminIssueCodesRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    if payload.kind not in ("invite", "trial", "recharge"):
        raise ApiError("BAD_REQUEST", "kind 必须为 invite/trial/recharge")
    if payload.kind == "recharge" and payload.amount <= 0:
        raise ApiError("BAD_REQUEST", "充值码必须指定 amount > 0")

    expireAt = datetime.now(UTC) + timedelta(days=payload.expireDays)
    codes: list[str] = []
    for _ in range(payload.count):
        codeBody = _genCodeBody({"invite": "INV", "trial": "TRY", "recharge": "RCH"}[payload.kind])
        if payload.kind == "invite":
            model = InviteCode(
                code=codeBody,
                maxUses=1,
                grantedBalance=payload.grantedBalance,
                grantedDays=payload.grantedDays,
                tier=UserTier(payload.tier),
                expireAt=expireAt,
            )
        elif payload.kind == "trial":
            model = TrialCode(
                code=codeBody,
                grantedBalance=payload.grantedBalance,
                grantedDays=payload.grantedDays,
                tier=UserTier.TRIAL,
                expireAt=expireAt,
            )
        else:
            model = RechargeCode(
                code=codeBody,
                amount=payload.amount,
                expireAt=expireAt,
                note="admin issued",
            )
        payloadDict = model.model_dump(mode="json")
        payloadDict["signature"] = hmacUtil.signPayload(payloadDict)
        import base64
        import json as _json

        raw = base64.b64encode(
            _json.dumps(payloadDict, ensure_ascii=False).encode("utf-8")
        ).decode("ascii")
        codes.append(raw)

    _audit(
        action="admin.issue_codes",
        details={
            "kind": payload.kind,
            "count": payload.count,
            "grantedBalance": payload.grantedBalance,
            "grantedDays": payload.grantedDays,
            "amount": payload.amount,
        },
    )
    logger.info(f"[Admin] issue_codes kind={payload.kind} count={payload.count}")
    return jsonify(AdminIssueCodesResponse(codes=codes).model_dump())


# ===========================================================================
# 2026-08-05 M2 B2 新增:用户管理
# ===========================================================================


@bp.get("/users")
@requireAdminCookie
def listUsers():
    """用户列表(分页 + 模糊搜索 displayName/userId)。"""
    limit = int(request.args.get("limit", 50))
    limit = max(1, min(200, limit))
    cursor = request.args.get("cursor")
    q = (request.args.get("q") or "").strip()

    with _sessionCtx() as db:
        stmt = (
            select(UserAccount, UserBalance)
            .outerjoin(UserBalance, UserBalance.userId == UserAccount.userId)
        )
        if cursor:
            try:
                cursorDt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            except ValueError as e:
                raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e
            stmt = stmt.where(UserAccount.createdAt < cursorDt)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                (UserAccount.displayName.like(like)) | (UserAccount.userId.like(like))
            )
        stmt = stmt.order_by(UserAccount.createdAt.desc()).limit(limit + 1)
        rows = db.execute(stmt).all()

        nextCursor: str | None = None
        if len(rows) > limit:
            lastRow = rows[limit - 1][0]
            nextCursor = lastRow.createdAt.isoformat()
            rows = rows[:limit]

        items = []
        for acct, bal in rows:
            bal = bal or UserBalance(userId=acct.userId)
            items.append(
                AdminUserListItem(
                    userId=acct.userId,
                    displayName=acct.displayName,
                    tier=acct.tier,
                    status=acct.status,
                    balance=int(bal.balance or 0),
                    totalSpent=int(bal.totalSpent or 0),
                    totalRecharged=int(bal.totalRecharged or 0),
                    activatedAt=acct.activatedAt,
                )
            )
        return jsonify(
            AdminUserListResponse(
                items=items,
                nextCursor=nextCursor,
            ).model_dump(mode="json")
        )


@bp.get("/users/<string:userId>")
@requireAdminCookie
def getUserDetail(userId: str):
    """用户详情(含 balance / device 数)。"""
    with _sessionCtx() as db:
        acct = db.get(UserAccount, userId)
        if acct is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        balance = db.get(UserBalance, userId) or UserBalance(userId=userId)

        # 设备数
        deviceCount = int(
            db.execute(
                select(saFunc.count()).select_from(UserDevice).where(
                    UserDevice.userId == userId
                )
            ).scalar_one()
            or 0
        )

        # 最近设备活跃时间
        lastSeen = db.execute(
            select(saFunc.max(UserDevice.lastSeenAt)).where(
                UserDevice.userId == userId
            )
        ).scalar_one_or_none()

        out = AdminUserDetail(
            userId=acct.userId,
            displayName=acct.displayName,
            tier=acct.tier,
            status=acct.status,
            balance=int(balance.balance or 0),
            frozenBalance=int(balance.frozenBalance or 0),
            totalSpent=int(balance.totalSpent or 0),
            totalRecharged=int(balance.totalRecharged or 0),
            activatedAt=acct.activatedAt,
            expireAt=acct.expireAt,
            lastSeenAt=lastSeen,
            deviceCount=deviceCount,
        )
        return jsonify(out.model_dump(mode="json"))


@bp.post("/users/<string:userId>/revoke-sessions")
@requireAdminCookie
def revokeUserSessions(userId: str):
    """撤销某用户的所有 refresh_token(强制下线)。

    失败:用户不存在 → 404。
    """
    try:
        body = RevokeSessionsRequest.model_validate(
            request.get_json(force=True, silent=True) or {}
        )
    except ValidationError:
        body = RevokeSessionsRequest()

    now = datetime.now(UTC).replace(tzinfo=None)
    with _sessionCtx() as db:
        if db.get(UserAccount, userId) is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        result = db.execute(
            select(RefreshToken).where(
                RefreshToken.userId == userId,
                RefreshToken.revokedAt.is_(None),
            )
        ).scalars().all()
        revoked = 0
        for tok in result:
            tok.revokedAt = now
            revoked += 1
        db.commit()

    _audit(
        action="admin.revoke_sessions",
        targetUser=userId,
        details={"revokedCount": revoked, "reason": body.reason},
    )
    logger.info(f"[Admin] revoke_sessions user={userId} count={revoked}")
    return jsonify(RevokeSessionsResponse(revokedCount=revoked, userId=userId).model_dump())


@bp.post("/users/<string:userId>/tier")
@requireAdminCookie
def updateUserTier(userId: str):
    """更新用户 tier(预留接口,本期保守只允许 active/beta/trial)。"""
    try:
        payload = AdminUpdateUserTierRequest.model_validate(
            request.get_json(force=True, silent=False)
        )
    except ValidationError as e:
        raise ApiError(
            "BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}
        ) from e

    with _sessionCtx() as db:
        user = db.get(UserAccount, userId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        oldTier = user.tier
        user.tier = payload.tier
        user.status = payload.status or user.status
        db.commit()

    _audit(
        action="admin.update_tier",
        targetUser=userId,
        details={"oldTier": oldTier, "newTier": payload.tier, "status": user.status},
    )
    return jsonify({"userId": userId, "tier": payload.tier, "status": user.status})


# ===========================================================================
# 2026-08-05 M2 B2 新增:审计日志
# ===========================================================================


@bp.get("/audit")
@requireAdminCookie
def listAudit():
    """审计日志查询(分页 + 时间范围 + action/actor 过滤)。"""
    limit = int(request.args.get("limit", 50))
    limit = max(1, min(200, limit))
    cursor = request.args.get("cursor")
    action = (request.args.get("action") or "").strip()
    actor = (request.args.get("actor") or "").strip()
    targetUser = (request.args.get("targetUser") or "").strip()
    days = request.args.get("days")

    with _sessionCtx() as db:
        stmt = select(AuditLog).order_by(AuditLog.createdAt.desc())
        if cursor:
            try:
                cursorDt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            except ValueError as e:
                raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e
            stmt = stmt.where(AuditLog.createdAt < cursorDt)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if actor:
            stmt = stmt.where(AuditLog.actor == actor)
        if targetUser:
            stmt = stmt.where(AuditLog.targetUser == targetUser)
        if days:
            try:
                daysInt = int(days)
            except ValueError as e:
                raise ApiError("BAD_REQUEST", f"days 格式错误: {e}") from e
            since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=daysInt)
            stmt = stmt.where(AuditLog.createdAt >= since)
        stmt = stmt.limit(limit + 1)
        rows = db.execute(stmt).scalars().all()

        nextCursor: str | None = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1].createdAt.isoformat()
            rows = rows[:limit]

        items = [
            AdminAuditItem(
                auditId=int(r.auditId),
                actor=r.actor,
                action=r.action,
                targetUser=r.targetUser,
                details=r.details,
                ip=r.ip,
                createdAt=r.createdAt,
            )
            for r in rows
        ]
        return jsonify(
            AdminAuditResponse(items=items, nextCursor=nextCursor).model_dump(mode="json")
        )


@bp.get("/audit-summary")
@requireAdminCookie
def auditSummary():
    """按 action group by + count(默认 7 天,看板用)。"""
    try:
        days = int(request.args.get("days", 7))
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"days 格式错误: {e}") from e
    days = max(1, min(90, days))
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)

    with _sessionCtx() as db:
        rows = db.execute(
            select(AuditLog.action, saFunc.count(AuditLog.auditId))
            .where(AuditLog.createdAt >= since)
            .group_by(AuditLog.action)
            .order_by(saFunc.count(AuditLog.auditId).desc())
        ).all()
        total = int(sum(c for _, c in rows) or 0)
        items = [
            AdminAuditSummaryItem(action=a, count=int(c)) for a, c in rows
        ]
        return jsonify(
            AdminAuditSummaryResponse(items=items, days=days, total=total).model_dump()
        )


# ===========================================================================
# 2026-08-05 M2 B2 新增:凭证查询 & 看板聚合
# ===========================================================================


@bp.get("/codes/lookup")
@requireAdminCookie
def codeLookup():
    """查询某个 RCH/INV/TRY 状态(hash 后查 codes_seen)。"""
    raw = (request.args.get("code") or "").strip()
    if not raw:
        raise ApiError("BAD_REQUEST", "缺少 code 参数")
    codeHash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    with _sessionCtx() as db:
        seen = db.get(LicenseCodeSeen, codeHash)
        if seen is None:
            return jsonify(
                CodeLookupResponse(
                    codeKind="unknown",
                    codeHash=codeHash,
                ).model_dump()
            )
        return jsonify(
            CodeLookupResponse(
                codeKind=seen.codeKind,
                codeHash=codeHash,
                consumedAt=seen.consumedAt,
                consumedByUserId=seen.consumedByUserId,
                rechargeAmount=int(seen.rechargeAmount) if seen.rechargeAmount is not None else None,
            ).model_dump(mode="json")
        )


@bp.get("/metrics-summary")
@requireAdminCookie
def metricsSummary():
    """看板聚合:用户总数 / 7 日活跃 / grant 总额 / 账单状态分布。"""
    with _sessionCtx() as db:
        userCount = int(
            db.execute(saFunc.count()).select_from(UserAccount).scalar_one() or 0
        )

        since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)

        sevenDayActive = int(
            db.execute(
                select(saFunc.count())
                .select_from(UserDevice)
                .where(UserDevice.lastSeenAt >= since)
            ).scalar_one()
            or 0
        )

        sevenDayGrantTotal = int(
            db.execute(
                select(saFunc.coalesce(saFunc.sum(RechargeRecord.amount), 0)).where(
                    RechargeRecord.createdAt >= since
                )
            ).scalar_one()
            or 0
        )

        billsPending = int(
            db.execute(
                select(saFunc.count())
                .select_from(Bill)
                .where(Bill.status == "pending")
            ).scalar_one()
            or 0
        )

        billsSettledLast7 = int(
            db.execute(
                select(saFunc.count())
                .select_from(Bill)
                .where(Bill.status == "settled", Bill.createdAt >= since)
            ).scalar_one()
            or 0
        )

        billsRefundedLast7 = int(
            db.execute(
                select(saFunc.count())
                .select_from(Bill)
                .where(Bill.status == "refunded", Bill.createdAt >= since)
            ).scalar_one()
            or 0
        )

        return jsonify(
            AdminMetricsSummary(
                userCount=userCount,
                sevenDayActive=sevenDayActive,
                sevenDayGrantTotal=sevenDayGrantTotal,
                billsPending=billsPending,
                billsSettledLast7Days=billsSettledLast7,
                billsRefundedLast7Days=billsRefundedLast7,
            ).model_dump()
        )
