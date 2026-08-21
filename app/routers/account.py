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
    """获取当前用户完整信息。

    返回身份 / 积分余额 / 当前活跃订阅 / 风控字段,是桌面端登录后
    首屏数据的唯一来源。

    ---
    tags: [account]
    security:
      - bearerAuth: []
    responses:
      200:
        description: 用户完整信息
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                userId: {type: integer}
                email: {type: string}
                displayName: {type: string}
                tier: {type: string}
                status: {type: string}
                balance: {type: integer, description: 总积分}
                reserved: {type: integer, description: 已冻结积分}
                available: {type: integer, description: 可用积分}
                emailVerified: {type: boolean}
                failedLoginCount: {type: integer}
                lockedUntil: {type: string, format: date-time, nullable: true}
                createdAt: {type: string, format: date-time}
                subscription:
                  type: object
                  nullable: true
                  description: 当前活跃订阅(None 表示 free)
                  properties:
                    subscriptionId: {type: integer}
                    planCode: {type: string}
                    status: {type: string}
                    startedAt: {type: string, format: date-time}
                    currentPeriodStart: {type: string, format: date-time}
                    currentPeriodEnd: {type: string, format: date-time}
                    expiresAt: {type: string, format: date-time}
                    autoRenew: {type: boolean}
                    monthlyQuota: {type: integer}
            requestId: {type: string}
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
    """
    with _sessionCtx() as db:
        result = accountGetMe(db, g.userId)
        return successEnvelope(result.model_dump(mode="json"))


@bp.patch("/me")
@requireUser
def patchMe():
    """更新当前用户资料(目前仅支持昵称)。

    ---
    tags: [account]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [displayName]
            properties:
              displayName:
                type: string
                maxLength: 64
                description: 新昵称
    responses:
      200:
        description: 更新成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                userId: {type: integer}
                displayName: {type: string}
                updatedAt: {type: string, format: date-time}
            requestId: {type: string}
      400:
        description: 参数错误(BAD_REQUEST)
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
    """
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
    """账单流水列表(按时间倒序,cursor 分页)。

    ---
    tags: [account]
    security:
      - bearerAuth: []
    parameters:
      - name: limit
        in: query
        required: false
        schema:
          type: integer
          default: 50
          minimum: 1
          maximum: 200
        description: 每页条数
      - name: cursor
        in: query
        required: false
        schema:
          type: string
        description: 上一页返回的 nextCursor(createdAt ISO 字符串)
    responses:
      200:
        description: 账单列表
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                nextCursor: {type: string, nullable: true}
                items:
                  type: array
                  items:
                    type: object
                    properties:
                      billId: {type: string}
                      actionType: {type: string}
                      actionDisplayName: {type: string}
                      estimatedCost: {type: integer}
                      realCost: {type: integer}
                      resourceUsed: {type: integer}
                      status: {type: string}
                      description: {type: string}
                      pricingVersion: {type: string}
                      inputTokens: {type: integer}
                      outputTokens: {type: integer}
                      createdAt: {type: string, format: date-time}
                      settledAt: {type: string, format: date-time, nullable: true}
            requestId: {type: string}
      400:
        description: cursor 格式错误(BAD_REQUEST)
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
    """
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
    """当前 + 历史订阅列表(分页 cursor 形式,按 currentPeriodEnd desc)。

    ---
    tags: [account]
    security:
      - bearerAuth: []
    parameters:
      - name: limit
        in: query
        required: false
        schema:
          type: integer
          default: 50
          minimum: 1
          maximum: 200
        description: 每页条数
      - name: cursor
        in: query
        required: false
        schema:
          type: string
        description: 上一页返回的 nextCursor
    responses:
      200:
        description: 订阅列表
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                nextCursor: {type: string, nullable: true}
                items:
                  type: array
                  items:
                    type: object
                    properties:
                      subscriptionId: {type: integer}
                      planCode: {type: string}
                      status: {type: string}
                      startedAt: {type: string, format: date-time}
                      currentPeriodStart: {type: string, format: date-time}
                      currentPeriodEnd: {type: string, format: date-time}
                      expiresAt: {type: string, format: date-time}
                      autoRenew: {type: boolean}
                      monthlyQuota: {type: integer}
            requestId: {type: string}
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
    """
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
        return successEnvelope(SubscriptionListResponse(items=items, nextCursor=nextCursor).model_dump(mode="json"))


# ---------------------------------------------------------------------------
# /devices(P0-A,新)
# ---------------------------------------------------------------------------


@bp.get("/devices")
@requireUser
def listDevices():
    """当前用户已登录设备列表。

    ---
    tags: [account]
    security:
      - bearerAuth: []
    responses:
      200:
        description: 设备列表
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                maxActive: {type: integer, description: 允许的最大活跃设备数}
                activeCount: {type: integer, description: 当前活跃设备数}
                items:
                  type: array
                  items:
                    type: object
                    properties:
                      deviceId: {type: integer}
                      devicePublicId: {type: string}
                      deviceName: {type: string}
                      platform: {type: string}
                      status: {type: string}
                      firstSeenAt: {type: string, format: date-time}
                      lastSeenAt: {type: string, format: date-time}
                      revokedAt: {type: string, format: date-time, nullable: true}
                      isCurrent: {type: boolean, description: 是否当前请求设备}
            requestId: {type: string}
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
    """
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
    """吊销指定设备(该设备的 refresh token 全部失效)。

    ---
    tags: [account]
    security:
      - bearerAuth: []
    parameters:
      - name: deviceRecordId
        in: path
        required: true
        schema:
          type: integer
        description: 设备记录 ID(来自 GET /devices 的 deviceId)
    responses:
      200:
        description: 吊销成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                deviceId: {type: integer}
                revokedRefreshTokens: {type: integer}
                status: {type: string, example: revoked}
            requestId: {type: string}
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
      403:
        description: 不能吊销他人设备(FORBIDDEN)
      404:
        description: 设备不存在(DEVICE_NOT_FOUND)
    """
    currentDeviceId = g.deviceId
    with _sessionCtx() as db:
        revokedCount = accountRevokeDevice(db, g.userId, deviceRecordId, currentDeviceId)
    return successEnvelope({"deviceId": deviceRecordId, "revokedRefreshTokens": revokedCount, "status": "revoked"})


# ---------------------------------------------------------------------------
# /delete(P0-A,新)
# ---------------------------------------------------------------------------


@bp.post("/delete")
@requireUser
@auditAction("user.account_deleted")
def postDeleteAccount():
    """注销账号(软删,30 天后硬删)。

    需要密码确认 + confirm=true。成功后当前用户的所有 refresh token
    被吊销。

    ---
    tags: [account]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [password, confirm]
            properties:
              password:
                type: string
                description: 当前登录密码
              confirm:
                type: boolean
                description: 必须显式传 true
                example: true
    responses:
      200:
        description: 已受理注销
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                userId: {type: integer}
                status: {type: string, example: deleted}
                scheduledHardDeleteAt: {type: string, format: date-time}
                revokedRefreshTokens: {type: integer}
            requestId: {type: string}
      400:
        description: 参数错误或未确认(BAD_REQUEST)
      401:
        description: 密码错误或未登录(UNAUTHORIZED / WRONG_PASSWORD)
    """
    try:
        payload = DeleteAccountRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    if not payload.confirm:
        raise ApiError("BAD_REQUEST", "必须显式确认 confirm=true", httpStatus=400)

    currentDeviceId = g.deviceId
    with _sessionCtx() as db:
        revokedCount, scheduledAt = accountDeleteAccount(db, g.userId, payload.password, currentDeviceId)
    return successEnvelope(
        DeleteAccountResponse(
            userId=g.userId,
            status="deleted",
            scheduledHardDeleteAt=scheduledAt,
            revokedRefreshTokens=revokedCount,
        ).model_dump(mode="json")
    )


__all__ = ["bp"]
