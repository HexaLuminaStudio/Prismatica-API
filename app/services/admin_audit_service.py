"""Admin 审计服务(2026-08-06 重构):

- recordAudit(actor, action, targetUser?, details?, ip?) → 写 audit_logs
- listAudit(limit, cursor, action?, actor?, targetUser?, days?) → 分页列表
- auditSummary(days) → group by action + count(看板)
- listCodes(filter) → 分页(从 license_codes 表)
- lookupCode(rawCode) → 查某个码的状态
- metricsSummary() → 看板聚合
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import func as saFunc
from sqlalchemy import select

from app.db import getDb
from app.errors import ApiError
from app.models import AuditLog, Bill, LicenseCode, RechargeRecord, UserAccount, UserDevice
from app.security.hmac import hashCode

# ---------------------------------------------------------------------------
# 写入(独立事务,失败不影响主流程)
# ---------------------------------------------------------------------------


def recordAudit(
    actor: str,
    action: str,
    targetUser: str | None = None,
    details: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    """写审计日志(独立事务,失败不影响主流程)。"""
    try:
        with getDb() as db:
            db.add(
                AuditLog(
                    actor=actor,
                    action=action,
                    targetUser=targetUser,
                    details=details,
                    ip=ip,
                )
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AdminAudit] recordAudit 失败: {e}")


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def _parseCursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e


def listAudit(
    limit: int,
    cursor: str | None = None,
    action: str | None = None,
    actor: str | None = None,
    targetUser: str | None = None,
    days: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """审计日志分页查询 → 返回 (items, nextCursor)。"""
    limit = max(1, min(200, limit))
    with getDb() as db:
        stmt = select(AuditLog).order_by(AuditLog.createdAt.desc())
        cursorDt = _parseCursor(cursor)
        if cursorDt is not None:
            stmt = stmt.where(AuditLog.createdAt < cursorDt)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if actor:
            stmt = stmt.where(AuditLog.actor == actor)
        if targetUser:
            stmt = stmt.where(AuditLog.targetUser == targetUser)
        if days:
            since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=int(days))
            stmt = stmt.where(AuditLog.createdAt >= since)
        stmt = stmt.limit(limit + 1)
        rows = db.execute(stmt).scalars().all()

        nextCursor: str | None = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1].createdAt.isoformat()
            rows = rows[:limit]

        items = [
            {
                "auditId": int(r.auditId),
                "actor": r.actor,
                "action": r.action,
                "targetUser": r.targetUser,
                "details": r.details,
                "ip": r.ip,
                "createdAt": r.createdAt,
            }
            for r in rows
        ]
        return items, nextCursor


def auditSummary(days: int = 7) -> dict[str, Any]:
    """按 action group by + count(看板用)。"""
    days = max(1, min(90, int(days)))
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    with getDb() as db:
        rows = db.execute(
            select(AuditLog.action, saFunc.count(AuditLog.auditId))
            .where(AuditLog.createdAt >= since)
            .group_by(AuditLog.action)
            .order_by(saFunc.count(AuditLog.auditId).desc())
        ).all()
        total = int(sum(c for _, c in rows) or 0)
        items = [{"action": a, "count": int(c)} for a, c in rows]
    return {"items": items, "days": days, "total": total}


# ---------------------------------------------------------------------------
# 凭证查询(2026-08-06 重构:基于 license_codes 表)
# ---------------------------------------------------------------------------


def listCodes(
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """凭证列表(不含明文 code)。"""
    limit = max(1, min(200, limit))
    with getDb() as db:
        stmt = select(LicenseCode).order_by(LicenseCode.issuedAt.desc())
        cursorDt = _parseCursor(cursor)
        if cursorDt is not None:
            stmt = stmt.where(LicenseCode.issuedAt < cursorDt)
        if kind:
            kindMap = {"invite": "INV", "trial": "TRY", "recharge": "RCH"}
            stmt = stmt.where(LicenseCode.codeKind == kindMap.get(kind, kind))
        if status:
            stmt = stmt.where(LicenseCode.status == status)
        stmt = stmt.limit(limit + 1)
        rows = db.execute(stmt).scalars().all()

        nextCursor: str | None = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1].issuedAt.isoformat()
            rows = rows[:limit]

        items = [
            {
                "codeHash": r.codeHash,
                "codeKind": {"INV": "invite", "TRY": "trial", "RCH": "recharge"}.get(r.codeKind, r.codeKind),
                "status": "consumed" if r.status == "exhausted" else r.status,
                "grantedBalance": r.monthlyQuota if r.codeKind in {"INV", "TRY"} else None,
                "grantedDays": (
                    (r.periodMonths * 30 if r.periodMonths else None)
                    if r.codeKind == "INV"
                    else r.trialDays
                ),
                "tier": r.planCode if r.codeKind == "INV" else ("pro" if r.codeKind == "TRY" else None),
                "amount": r.amount,
                "issuedBy": r.issuedBy or "admin",
                "issuedAt": r.issuedAt,
                "expireAt": r.expiresAt,
                "consumedAt": None,
                "consumedByUserId": None,
                "consumedIp": None,
            }
            for r in rows
        ]
        return items, nextCursor


def lookupCode(rawCode: str) -> dict[str, Any]:
    """按明文 code 查状态(hash 后查 license_codes)。"""
    if not rawCode:
        raise ApiError("BAD_REQUEST", "缺少 code 参数")
    codeHash = hashCode(rawCode)
    with getDb() as db:
        row = db.execute(select(LicenseCode).where(LicenseCode.codeHash == codeHash)).scalar_one_or_none()
        if row is None:
            return {
                "codeKind": "unknown",
                "codeHash": codeHash,
                "status": "unknown",
                "consumedAt": None,
                "consumedByUserId": None,
                "rechargeAmount": None,
            }
        return {
            "codeKind": {"INV": "invite", "TRY": "trial", "RCH": "recharge"}.get(row.codeKind, row.codeKind),
            "codeHash": row.codeHash,
            "status": "consumed" if row.status == "exhausted" else row.status,
            "consumedAt": None,
            "consumedByUserId": None,
            "rechargeAmount": int(row.amount) if row.amount is not None else None,
        }


def revokeCode(codeHash: str) -> dict[str, Any]:
    """撤销某凭证(active → revoked)。"""
    with getDb() as db:
        row = db.execute(select(LicenseCode).where(LicenseCode.codeHash == codeHash)).scalar_one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", "凭证不存在")
        if row.status == "consumed":
            raise ApiError("CONFLICT", "凭证已消费,无法撤销")
        if row.status == "revoked":
            return {"codeHash": row.codeHash, "status": row.status}
        row.status = "revoked"
        db.commit()
        return {"codeHash": row.codeHash, "status": row.status}


# ---------------------------------------------------------------------------
# 看板聚合
# ---------------------------------------------------------------------------


def metricsSummary() -> dict[str, Any]:
    """看板 KPI:用户总数 / 7 日活跃 / grant 总额 / 账单状态分布。"""
    with getDb() as db:
        userCount = int(
            db.execute(
                select(saFunc.count()).select_from(UserAccount)
            ).scalar_one()
            or 0
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

    return {
        "userCount": userCount,
        "sevenDayActive": sevenDayActive,
        "sevenDayGrantTotal": sevenDayGrantTotal,
        "billsPending": billsPending,
        "billsSettledLast7Days": billsSettledLast7,
        "billsRefundedLast7Days": billsRefundedLast7,
    }


__all__ = [
    "recordAudit",
    "listAudit",
    "auditSummary",
    "listCodes",
    "lookupCode",
    "revokeCode",
    "metricsSummary",
]
