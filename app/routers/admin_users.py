"""/v1/admin/users/* 路由(2026-08-07 运营管理增强)。

端点:
    GET    /v1/admin/users                       列表 + 多维筛选
    POST   /v1/admin/users                       新建用户
    GET    /v1/admin/users/{userId}              详情
    PATCH  /v1/admin/users/{userId}              修改 tier / status / email / displayName
    DELETE /v1/admin/users/{userId}              删除(需 confirm)
    POST   /v1/admin/users/{userId}/grant        加余额
    POST   /v1/admin/users/{userId}/reset-password 管理员重置密码
    POST   /v1/admin/users/{userId}/revoke-sessions 撤销该用户 refresh_token
    POST   /v1/admin/users/{userId}/devices/{deviceId}/revoke 撤销设备
    GET    /v1/admin/users/{userId}/subscriptions 订阅
    GET    /v1/admin/users/{userId}/devices      设备
    GET    /v1/admin/users/{userId}/ledger       账本
    POST   /v1/admin/users/batch                 批量操作

业务逻辑全部由 admin_user_service 承载,本文件仅做参数解析 + 响应组装。
"""

from __future__ import annotations

from flask import Blueprint, request
from pydantic import ValidationError

from app.deps import requireAdminCookie
from app.errors import ApiError, successEnvelope
from app.schemas.admin import (
    AdminBatchUsersRequest,
    AdminBatchUsersResponse,
    AdminCreateSubscriptionRequest,
    AdminCreateSubscriptionResponse,
    AdminCreateUserRequest,
    AdminCreateUserResponse,
    AdminDeleteUserResponse,
    AdminGrantBalanceRequest,
    AdminGrantBalanceResponse,
    AdminResetUserPasswordResponse,
    AdminRevokeSessionsRequest,
    AdminRevokeSessionsResponse,
    AdminRevokeUserDeviceResponse,
    AdminUpdateUserRequest,
    AdminUpdateUserResponse,
    AdminUserDetail,
    AdminUserDeviceItem,
    AdminUserDevicesResponse,
    AdminUserLedgerItem,
    AdminUserLedgerResponse,
    AdminUserListItem,
    AdminUserListResponse,
    AdminUserSubscriptionItem,
    AdminUserSubscriptionsResponse,
)
from app.services.admin_user_service import (
    batchUsers,
    createUser,
    createUserSubscription,
    deleteUser,
    getUserDetail,
    grantBalance,
    listUserDevices,
    listUserLedger,
    listUsers,
    listUserSubscriptions,
    resetUserPassword,
    revokeAllSessions,
    revokeUserDevice,
    updateUser,
)

bp = Blueprint("admin_users", __name__, url_prefix="/v1/admin/users")


@bp.get("")
@requireAdminCookie
def listUsersRoute():
    """用户列表(分页 + 多维筛选 status / tier / 注册时间 / 关键词)。"""
    limit = int(request.args.get("limit", 50))
    cursor = request.args.get("cursor")
    items, nextCursor = listUsers(
        limit=limit,
        cursor=cursor,
        q=(request.args.get("q") or "").strip() or None,
        status=request.args.get("status") or None,
        tier=request.args.get("tier") or None,
        registeredAfter=request.args.get("registeredAfter") or None,
        registeredBefore=request.args.get("registeredBefore") or None,
    )
    data = AdminUserListResponse(
        items=[AdminUserListItem(**item) for item in items],
        nextCursor=nextCursor,
    ).model_dump(mode="json")
    return successEnvelope(data)


@bp.post("")
@requireAdminCookie
def createUserRoute():
    """新建用户(邮箱 + 初始密码 + tier/status)。"""
    try:
        payload = AdminCreateUserRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e
    raw = createUser(
        email=payload.email,
        password=payload.password,
        displayName=payload.displayName,
        tier=payload.tier,
        status=payload.status,
    )
    data = AdminCreateUserResponse(**raw).model_dump(mode="json")
    return successEnvelope(data)


@bp.post("/batch")
@requireAdminCookie
def batchUsersRoute():
    """批量操作:update_status / reset_password / delete。"""
    try:
        payload = AdminBatchUsersRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e
    result = batchUsers(
        action=payload.action,
        userIds=payload.userIds,
        status=payload.status,
    )
    data = AdminBatchUsersResponse(**result).model_dump()
    return successEnvelope(data)


@bp.get("/<string:userId>")
@requireAdminCookie
def getUserDetailRoute(userId: str):
    """用户详情(含 balance / device 数 / 累计赠送/消费)。"""
    raw = getUserDetail(userId)
    data = AdminUserDetail(**raw).model_dump(mode="json")
    return successEnvelope(data)


@bp.patch("/<string:userId>")
@requireAdminCookie
def updateUserRoute(userId: str):
    """修改 tier / status / email / displayName(任一字段可选)。"""
    try:
        payload = AdminUpdateUserRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e
    result = updateUser(
        userId=userId,
        tier=payload.tier,
        status=payload.status,
        email=payload.email,
        displayName=payload.displayName,
    )
    data = AdminUpdateUserResponse(**result).model_dump()
    return successEnvelope(data)


@bp.delete("/<string:userId>")
@requireAdminCookie
def deleteUserRoute(userId: str):
    """永久删除用户及其关联数据(需 confirm 等于 userId)。"""
    confirm = (request.args.get("confirm") or "").strip()
    result = deleteUser(userId=userId, confirm=confirm)
    data = AdminDeleteUserResponse(**result).model_dump()
    return successEnvelope(data)


@bp.post("/<string:userId>/grant")
@requireAdminCookie
def grantBalanceRoute(userId: str):
    """手动加余额(写 balance_ledger + audit_logs)。"""
    try:
        payload = AdminGrantBalanceRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e
    result = grantBalance(userId=userId, amount=payload.amount, note=payload.note)
    data = AdminGrantBalanceResponse(**result).model_dump()
    return successEnvelope(data)


@bp.post("/<string:userId>/reset-password")
@requireAdminCookie
def resetPasswordRoute(userId: str):
    """管理员重置用户密码(返回一次性明文 + 撤销其所有 session)。"""
    result = resetUserPassword(userId=userId)
    data = AdminResetUserPasswordResponse(**result).model_dump()
    return successEnvelope(data)


@bp.post("/<string:userId>/revoke-sessions")
@requireAdminCookie
def revokeSessionsRoute(userId: str):
    """撤销该用户所有 refresh_token。"""
    try:
        body = request.get_json(force=True, silent=True) or {}
        payload = AdminRevokeSessionsRequest.model_validate(body)
    except ValidationError:
        payload = AdminRevokeSessionsRequest()
    result = revokeAllSessions(userId=userId, reason=payload.reason)
    data = AdminRevokeSessionsResponse(**result).model_dump()
    return successEnvelope(data)


@bp.get("/<string:userId>/subscriptions")
@requireAdminCookie
def listSubscriptionsRoute(userId: str):
    items = listUserSubscriptions(userId=userId)
    data = AdminUserSubscriptionsResponse(items=[AdminUserSubscriptionItem(**item) for item in items]).model_dump(
        mode="json"
    )
    return successEnvelope(data)


@bp.post("/<string:userId>/subscriptions")
@requireAdminCookie
def createSubscriptionRoute(userId: str):
    """为用户开通试用、Pro 月度或 Team 月度订阅。"""
    try:
        payload = AdminCreateSubscriptionRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError(
            "BAD_REQUEST",
            "请求参数错误",
            details={"errors": error.errors()},
        ) from error
    result = createUserSubscription(userId, payload.planCode)
    data = AdminCreateSubscriptionResponse(**result).model_dump(mode="json")
    return successEnvelope(data, httpStatus=201)


@bp.get("/<string:userId>/devices")
@requireAdminCookie
def listDevicesRoute(userId: str):
    items = listUserDevices(userId=userId)
    data = AdminUserDevicesResponse(items=[AdminUserDeviceItem(**item) for item in items]).model_dump(mode="json")
    return successEnvelope(data)


@bp.post("/<string:userId>/devices/<string:deviceId>/revoke")
@requireAdminCookie
def revokeDeviceRoute(userId: str, deviceId: str):
    result = revokeUserDevice(userId=userId, deviceId=deviceId)
    data = AdminRevokeUserDeviceResponse(**result).model_dump()
    return successEnvelope(data)


@bp.get("/<string:userId>/ledger")
@requireAdminCookie
def listLedgerRoute(userId: str):
    limit = int(request.args.get("limit", 20))
    items = listUserLedger(userId=userId, limit=limit)
    data = AdminUserLedgerResponse(items=[AdminUserLedgerItem(**item) for item in items]).model_dump(mode="json")
    return successEnvelope(data)


__all__ = ["bp"]
