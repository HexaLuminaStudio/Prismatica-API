"""/v1/admin/users/* 路由(2026-08-06 重构)。

端点:
    GET    /v1/admin/users                         列表 + 模糊搜索
    GET    /v1/admin/users/{userId}                详情
    PATCH  /v1/admin/users/{userId}                修改 tier / status
    POST   /v1/admin/users/{userId}/grant          加余额
    POST   /v1/admin/users/{userId}/revoke-sessions 撤销该用户 refresh_token

业务逻辑全部由 admin_user_service 承载,本文件仅做参数解析 + 响应组装。
"""
from __future__ import annotations

from flask import Blueprint, request
from pydantic import ValidationError

from app.deps import requireAdminCookie
from app.errors import ApiError, successEnvelope
from app.schemas.admin import (
    AdminGrantBalanceRequest,
    AdminGrantBalanceResponse,
    AdminRevokeSessionsRequest,
    AdminRevokeSessionsResponse,
    AdminUpdateUserRequest,
    AdminUpdateUserResponse,
    AdminUserDetail,
    AdminUserListItem,
    AdminUserListResponse,
)
from app.services.admin_user_service import (
    getUserDetail,
    grantBalance,
    listUsers,
    revokeAllSessions,
    updateUserTier,
)

bp = Blueprint("admin_users", __name__, url_prefix="/v1/admin/users")


@bp.get("")
@requireAdminCookie
def listUsersRoute():
    """用户列表(分页 + 模糊搜索 displayName/userId)。"""
    limit = int(request.args.get("limit", 50))
    cursor = request.args.get("cursor")
    q = (request.args.get("q") or "").strip()

    items, nextCursor = listUsers(limit=limit, cursor=cursor, q=q)
    data = AdminUserListResponse(
        items=[AdminUserListItem(**i) for i in items],
        nextCursor=nextCursor,
    ).model_dump(mode="json")
    return successEnvelope(data)


@bp.get("/<string:userId>")
@requireAdminCookie
def getUserDetailRoute(userId: str):
    """用户详情(含 balance / device 数)。"""
    raw = getUserDetail(userId)
    data = AdminUserDetail(**raw).model_dump(mode="json")
    return successEnvelope(data)


@bp.patch("/<string:userId>")
@requireAdminCookie
def updateUserRoute(userId: str):
    """修改用户 tier / status。"""
    try:
        payload = AdminUpdateUserRequest.model_validate(
            request.get_json(force=True, silent=False)
        )
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    result = updateUserTier(userId=userId, tier=payload.tier, status=payload.status)
    data = AdminUpdateUserResponse(**result).model_dump()
    return successEnvelope(data)


@bp.post("/<string:userId>/grant")
@requireAdminCookie
def grantBalanceRoute(userId: str):
    """手动加余额(写 recharge_records + audit_logs)。"""
    try:
        payload = AdminGrantBalanceRequest.model_validate(
            request.get_json(force=True, silent=False)
        )
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    result = grantBalance(userId=userId, amount=payload.amount, note=payload.note)
    data = AdminGrantBalanceResponse(**result).model_dump()
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


__all__ = ["bp"]
