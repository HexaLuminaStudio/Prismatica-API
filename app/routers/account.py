"""/v1/account/* 路由:me / patch / bills / devices / delete / subscriptions。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime

from flask import Blueprint, g, request
from pydantic import ValidationError
from sqlalchemy import select

from app.db import getDb
from app.deps import requireUser
from app.errors import ApiError, successEnvelope
from app.middleware.audit_log import auditAction
from app.models import Bill, Subscription
from app.schemas.account import (
    DeleteAccountRequest,
    DeleteAccountResponse,
    DeviceListResponse,
    MePatchRequest,
    MePatchResponse,
    SubscriptionListResponse,
    SubscriptionOut,
)
from app.schemas.user import BillListResponse, BillOut
from app.services.account_service import (
    deleteAccount as accountDeleteAccount,
)
from app.services.account_service import getMe as accountGetMe
from app.services.account_service import (
    listDevices as accountListDevices,
)
from app.services.account_service import (
    patchMe as accountPatchMe,
)
from app.services.account_service import (
    revokeDevice as accountRevokeDevice,
)

bp = Blueprint("account", __name__, url_prefix="/v1/account")


@contextmanager
def _sessionCtx():
    with getDb() as db:
        yield db


# ---------------------------------------------------------------------------
# /me(P0-A 完整版,含订阅 + 风控字段)
# ---------------------------------------------------------------------------


@bp.get("/me")
@requireUser
def getMe():
    with _sessionCtx() as db:
        result = accountGetMe(db, g.userId)
        return successEnvelope(result.model_dump(mode="json"))


@bp.patch("/me")
@requireUser
def patchMe():
    try:
        payload = MePatchRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        resp: MePatchResponse = accountPatchMe(db, g.userId, payload.displayName)
        return successEnvelope(resp.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# /bills(旧版,沿用 BillOut 兼容 UI)
# ---------------------------------------------------------------------------


@bp.get("/bills")
@requireUser
def getBills():
    limit = int(request.args.get("limit", 50))
    limit = max(1, min(200, limit))
    cursor = request.args.get("cursor")  # createdAt ISO 字符串(分页锚点)

    with _sessionCtx() as db:
        stmt = select(Bill).where(Bill.userId == g.userId)
        if cursor:
            try:
                cursorDt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            except ValueError as e:
                raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e
            stmt = stmt.where(Bill.createdAt < cursorDt)
        stmt = stmt.order_by(Bill.createdAt.desc()).limit(limit + 1)
        rows = db.execute(stmt).scalars().all()

        nextCursor: str | None = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1].createdAt.isoformat()
            rows = rows[:limit]

        items = [
            BillOut(
                billId=r.billId,
                actionType=r.feature,
                actionDisplayName=str((r.pricingSnapshot or {}).get("displayName") or r.feature),
                estimatedCost=r.estimatedCost,
                realCost=int(r.actualCost or 0),
                resourceUsed=int(r.inputTokens or 0) + int(r.outputTokens or 0),
                balanceBefore=0,
                balanceAfter=0,
                status=r.status,
                taskId="",
                description=r.description,
                pricingVersion=r.pricingVersion,
                inputTokens=r.inputTokens,
                outputTokens=r.outputTokens,
                createdAt=r.createdAt,
                settledAt=r.settledAt,
            )
            for r in rows
        ]
        return successEnvelope(BillListResponse(items=items, nextCursor=nextCursor).model_dump(mode="json"))


# ---------------------------------------------------------------------------
# /subscriptions(P0-A,新)
# ---------------------------------------------------------------------------


@bp.get("/subscriptions")
@requireUser
def listSubscriptions():
    """当前 + 历史订阅(分页 cursor 形式,按 current_period_end desc)。"""
    limit = int(request.args.get("limit", 50))
    limit = max(1, min(200, limit))
    cursor = request.args.get("cursor")

    with _sessionCtx() as db:
        stmt = select(Subscription).where(Subscription.userId == g.userId)
        if cursor:
            try:
                cursorDt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            except ValueError as e:
                raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e
            stmt = stmt.where(Subscription.currentPeriodEnd < cursorDt)
        stmt = stmt.order_by(Subscription.currentPeriodEnd.desc()).limit(limit + 1)
        rows = db.execute(stmt).scalars().all()

        nextCursor: str | None = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1].currentPeriodEnd.isoformat()
            rows = rows[:limit]
        items = [
            SubscriptionOut(
                subscriptionId=r.id,
                planCode=r.planCode,
                status=r.status,
                startedAt=r.startedAt,
                currentPeriodStart=r.currentPeriodStart,
                currentPeriodEnd=r.currentPeriodEnd,
                expiresAt=r.expiresAt,
                autoRenew=bool(r.autoRenew),
                monthlyQuota=int(r.monthlyQuota or 0),
            )
            for r in rows
        ]
        return successEnvelope(
            SubscriptionListResponse(items=items, nextCursor=nextCursor).model_dump(mode="json")
        )


# ---------------------------------------------------------------------------
# /devices(P0-A,新)
# ---------------------------------------------------------------------------


@bp.get("/devices")
@requireUser
def listDevices():
    currentDeviceId = g.deviceId
    with _sessionCtx() as db:
        items, maxActive, activeCount = accountListDevices(db, g.userId, currentDeviceId)
        body = DeviceListResponse(
            items=items,
            maxActive=maxActive,
            activeCount=activeCount,
        ).model_dump(mode="json")
        return successEnvelope(body)


@bp.delete("/devices/<int:deviceRecordId>")
@requireUser
@auditAction("user.device_revoke", targetType="user_device")
def deleteDevice(deviceRecordId: int):
    currentDeviceId = g.deviceId
    with _sessionCtx() as db:
        revokedCount = accountRevokeDevice(db, g.userId, deviceRecordId, currentDeviceId)
    return successEnvelope(
        {"deviceId": deviceRecordId, "revokedRefreshTokens": revokedCount, "status": "revoked"}
    )


# ---------------------------------------------------------------------------
# /delete(P0-A,新)
# ---------------------------------------------------------------------------


@bp.post("/delete")
@requireUser
@auditAction("user.account_deleted")
def postDeleteAccount():
    try:
        payload = DeleteAccountRequest.model_validate(
            request.get_json(force=True, silent=False)
        )
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    if not payload.confirm:
        raise ApiError("BAD_REQUEST", "必须显式确认 confirm=true", httpStatus=400)

    currentDeviceId = g.deviceId
    with _sessionCtx() as db:
        revokedCount, scheduledAt = accountDeleteAccount(
            db, g.userId, payload.password, currentDeviceId
        )
    return successEnvelope(
        DeleteAccountResponse(
            userId=g.userId,
            status="deleted",
            scheduledHardDeleteAt=scheduledAt,
            revokedRefreshTokens=revokedCount,
        ).model_dump(mode="json")
    )


__all__ = ["bp"]
