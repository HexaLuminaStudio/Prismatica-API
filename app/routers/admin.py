# coding: utf-8
"""/v1/admin/* 路由:grant / issue-codes(运营 CLI 调用)。"""
from __future__ import annotations

import secrets
import string
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, request
from loguru import logger
from pydantic import ValidationError

from app.db import getDb
from app.deps import getClientIp, requireAdmin
from app.errors import ApiError
from app.models import AuditLog, RechargeRecord, UserAccount, UserBalance
from app.schemas.admin import (
    AdminGrantRequest,
    AdminGrantResponse,
    AdminIssueCodesRequest,
    AdminIssueCodesResponse,
)
from app.security import hmac as hmacUtil
from app.models.license_models import InviteCode, RechargeCode, TrialCode, UserTier

bp = Blueprint("admin", __name__, url_prefix="/v1/admin")


@contextmanager
def _sessionCtx():
    with getDb() as db:
        yield db


def _audit(action: str, targetUser: str = None, details: dict = None) -> None:
    """写审计日志(独立事务,失败不影响主流程)。"""
    try:
        with getDb() as db:
            db.add(
                AuditLog(
                    actor=getattr(g, "adminActor", "admin"),
                    action=action,
                    targetUser=targetUser,
                    details=details,
                    ip=getClientIp(),
                )
            )
    except Exception as e:
        logger.warning(f"[Audit] 写日志失败: {e}")


def _genCodeBody(prefix: str) -> str:
    """形如 INV-AB12-CD34-EF56-GH78 的码体(排除 0/O/1/I/L)。"""
    alphabet = "".join(
        c for c in (string.ascii_uppercase + string.digits) if c not in "0O1IL"
    )
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return f"{prefix}-" + "-".join(parts)


@bp.post("/grant")
@requireAdmin
def postGrant():
    try:
        payload = AdminGrantRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()})

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
    try:
        payload = AdminIssueCodesRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()})

    if payload.kind not in ("invite", "trial", "recharge"):
        raise ApiError("BAD_REQUEST", "kind 必须为 invite/trial/recharge")
    if payload.kind == "recharge" and payload.amount <= 0:
        raise ApiError("BAD_REQUEST", "充值码必须指定 amount > 0")

    expireAt = datetime.now(timezone.utc) + timedelta(days=payload.expireDays)
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
        # 复用客户端同款 HMAC 签名格式
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
    logger.info(
        f"[Admin] issue_codes kind={payload.kind} count={payload.count}"
    )
    return jsonify(AdminIssueCodesResponse(codes=codes).model_dump())