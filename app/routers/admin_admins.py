"""/v1/admin/admins/* 路由(2026-08-06 M3 新增)。

端点:
    GET    /v1/admin/admins                       列表 + 过滤(分页)
    POST   /v1/admin/admins                       创建账号
    PATCH  /v1/admin/admins/{userId}              修改 status / role
    DELETE /v1/admin/admins/{userId}?confirm=...  软删除(二次确认)
    POST   /v1/admin/admins/{userId}/reset-password  重置密码(返回一次性明文)

所有端点必须 @requireAdminCookie + @requireOwner。
"""

from __future__ import annotations

from flask import Blueprint, g, request
from pydantic import ValidationError

from app.deps import requireAdminCookie, requireOwner
from app.errors import ApiError, successEnvelope
from app.schemas.admin import (
    AdminAccountListResponse,
    AdminCreateAdminRequest,
    AdminCreateAdminResponse,
    AdminDeleteAdminResponse,
    AdminResetPasswordResponse,
    AdminUpdateAdminRequest,
    AdminUpdateAdminResponse,
)
from app.services.admin_account_service import (
    createAdmin,
    listAdmins,
    resetAdminPassword,
    setAdminStatus,
    softDeleteAdmin,
    updateAdminRole,
)

bp = Blueprint("admin_admins", __name__, url_prefix="/v1/admin/admins")


def _actor() -> str:
    """拿当前管理员 username(供 audit_logs.actor)。"""
    return getattr(g, "adminUsername", None) or getattr(g, "adminActor", "admin")


def _actorUserId() -> str | None:
    return getattr(g, "adminUserId", None)


@bp.get("")
@requireAdminCookie
@requireOwner
def listAdminsRoute():
    """账号列表(分页 + 过滤,仅 owner)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: limit
        in: query
        required: false
        schema: {type: integer, default: 50}
      - name: cursor
        in: query
        required: false
        schema: {type: string}
      - name: q
        in: query
        required: false
        schema: {type: string}
      - name: status
        in: query
        required: false
        schema: {type: string}
      - name: role
        in: query
        required: false
        schema: {type: string}
    responses:
      200:
        description: 账号列表
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      403:
        description: 非 owner 无权限(FORBIDDEN)
    """
    limit = int(request.args.get("limit", 50))
    cursor = request.args.get("cursor")
    q = (request.args.get("q") or "").strip() or None
    status = (request.args.get("status") or "").strip() or None
    role = (request.args.get("role") or "").strip() or None

    items, nextCursor = listAdmins(limit=limit, cursor=cursor, q=q, status=status, role=role)
    data = AdminAccountListResponse(
        items=items,
        nextCursor=nextCursor,
    ).model_dump(mode="json")
    return successEnvelope(data)


@bp.post("")
@requireAdminCookie
@requireOwner
def postCreateAdmin():
    """创建账号(仅 owner)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [username, password]
            properties:
              username: {type: string}
              password: {type: string, minLength: 8}
              role: {type: string, enum: [admin, operator], default: operator}
    responses:
      200:
        description: 创建成功
      400:
        description: 参数错误(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      403:
        description: 非 owner 无权限(FORBIDDEN)
    """
    try:
        payload = AdminCreateAdminRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    result = createAdmin(
        username=payload.username,
        password=payload.password,
        role=payload.role,
        actor=_actor(),
    )
    data = AdminCreateAdminResponse(**result).model_dump(mode="json")
    return successEnvelope(data)


@bp.patch("/<string:userId>")
@requireAdminCookie
@requireOwner
def patchUpdateAdmin(userId: str):
    """修改 status 或 role(两者均可独立传,仅 owner)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: userId
        in: path
        required: true
        schema: {type: string}
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            properties:
              status: {type: string, enum: [active, disabled]}
              role: {type: string, enum: [admin, operator]}
    responses:
      200:
        description: 修改成功
      400:
        description: status 与 role 至少传一个(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      403:
        description: 非 owner 无权限(FORBIDDEN)
      404:
        description: 账号不存在(NOT_FOUND)
    """
    try:
        payload = AdminUpdateAdminRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    if payload.status is None and payload.role is None:
        raise ApiError("BAD_REQUEST", "status 与 role 至少传一个")

    actorUserId = _actorUserId()
    result: dict = {"userId": userId, "role": None, "status": None}

    if payload.status is not None:
        r = setAdminStatus(
            userId=userId,
            status=payload.status,
            actor=_actor(),
            actorUserId=actorUserId,
        )
        result["status"] = r["status"]

    if payload.role is not None:
        r = updateAdminRole(
            userId=userId,
            role=payload.role,
            actor=_actor(),
            actorUserId=actorUserId,
        )
        result["role"] = r["role"]

    # 兜底:从 DB 重读一次,保证 role/status 都是最新值
    if result["role"] is None or result["status"] is None:
        from app.db import getDb
        from app.models import AdminUser

        with getDb() as db:
            row = db.get(AdminUser, userId)
            if row is None or row.deletedAt is not None:
                raise ApiError("NOT_FOUND", "管理员账号不存在或已删除")
            result["role"] = result["role"] or row.role
            result["status"] = result["status"] or row.status

    data = AdminUpdateAdminResponse(**result).model_dump()
    return successEnvelope(data)


@bp.delete("/<string:userId>")
@requireAdminCookie
@requireOwner
def deleteSoftAdmin(userId: str):
    """软删除(接受 query confirm=<username> 二次确认,仅 owner)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: userId
        in: path
        required: true
        schema: {type: string}
      - name: confirm
        in: query
        required: true
        schema: {type: string}
        description: 被删账号的 username
    responses:
      200:
        description: 已软删除
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      403:
        description: 非 owner 无权限(FORBIDDEN)
      404:
        description: 账号不存在(NOT_FOUND)
    """
    confirm = (request.args.get("confirm") or "").strip()
    result = softDeleteAdmin(
        userId=userId,
        actor=_actor(),
        actorUserId=_actorUserId(),
        confirmUsername=confirm,
    )
    data = AdminDeleteAdminResponse(**result).model_dump(mode="json")
    return successEnvelope(data)


@bp.post("/<string:userId>/reset-password")
@requireAdminCookie
@requireOwner
def postResetPassword(userId: str):
    """重置密码(返回一次性明文,仅 owner)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: userId
        in: path
        required: true
        schema: {type: string}
    responses:
      200:
        description: 重置成功(含一次性明文密码)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      403:
        description: 非 owner 无权限(FORBIDDEN)
      404:
        description: 账号不存在(NOT_FOUND)
    """
    result = resetAdminPassword(
        userId=userId,
        actor=_actor(),
        actorUserId=_actorUserId(),
    )
    data = AdminResetPasswordResponse(**result).model_dump(mode="json")
    return successEnvelope(data)


__all__ = ["bp"]
