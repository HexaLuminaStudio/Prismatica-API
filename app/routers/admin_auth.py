"""/v1/admin/auth/* 路由(2026-08-06 重构)。

端点:
    POST /v1/admin/auth/login              用户名密码 → cookie
    POST /v1/admin/auth/logout             清除 cookie
    GET  /v1/admin/auth/me                 当前管理员信息
    POST /v1/admin/auth/change-password    修改自身密码
    GET  /v1/admin/health                  健康检查(无需鉴权)
"""
from __future__ import annotations

from flask import Blueprint, g, make_response, request
from pydantic import ValidationError

from app.db import getDb
from app.deps import getClientIp, requireAdminCookie
from app.errors import ApiError, successEnvelope
from app.health import buildHealthPayload
from app.middleware.admin_session import clearSessionCookie, setSessionCookie
from app.models import AdminUser
from app.schemas.admin import (
    AdminChangePasswordRequest,
    AdminChangePasswordResponse,
    AdminLoginRequest,
    AdminMeResponse,
)
from app.security.password import verifyPassword
from app.services.admin_auth_service import changePassword, loginByPassword

bp = Blueprint("admin_auth", __name__, url_prefix="/v1/admin")


@bp.post("/auth/login")
def postLogin():
    """用户名密码登录 → 颁 HttpOnly cookie(失败不颁)。

    错误:
        - 401 ADMIN_INVALID_CREDENTIALS
        - 423 ADMIN_ACCOUNT_LOCKED
    """
    try:
        payload = AdminLoginRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    with getDb() as db:
        user = loginByPassword(
            db,
            username=payload.username,
            password=payload.password,
            ip=getClientIp(),
        )
        data = AdminMeResponse(
            userId=user.userId,
            username=user.username,
            role=user.role,
            status=user.status,
            lastLoginAt=user.lastLoginAt,
        ).model_dump(mode="json")
        respBody, status = successEnvelope(data)
        resp = make_response(respBody, status)
        setSessionCookie(resp, userId=user.userId, username=user.username)
        return resp


@bp.post("/auth/logout")
def postLogout():
    """清除 cookie(无需鉴权:无 cookie 也是 200)。"""
    respBody, status = successEnvelope(None)
    resp = make_response(respBody, status)
    clearSessionCookie(resp)
    return resp


@bp.get("/auth/me")
@requireAdminCookie
def getMe():
    """返回当前登录管理员信息。"""
    userId = getattr(g, "adminUserId", "")
    if not userId or userId == "cli-admin":
        # CLI mode:仅返回占位信息,不查 DB
        data = AdminMeResponse(
            userId="cli-admin",
            username="cli-admin",
            role="admin",
            status="active",
            lastLoginAt=None,
        ).model_dump(mode="json")
        return successEnvelope(data)
    with getDb() as db:
        user = db.get(AdminUser, userId)
        if user is None:
            raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)
        data = AdminMeResponse(
            userId=user.userId,
            username=user.username,
            role=user.role,
            status=user.status,
            lastLoginAt=user.lastLoginAt,
        ).model_dump(mode="json")
        return successEnvelope(data)


@bp.post("/auth/change-password")
@requireAdminCookie
def postChangePassword():
    """修改自身密码(强制要求 ≥ 8 位)。"""
    try:
        payload = AdminChangePasswordRequest.model_validate(
            request.get_json(force=True, silent=False)
        )
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    userId = getattr(g, "adminUserId", "")
    if not userId or userId == "cli-admin":
        raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)

    with getDb() as db:
        user = db.get(AdminUser, userId)
        if user is None:
            raise ApiError("NOT_FOUND", "管理员账号不存在")
        if not verifyPassword(payload.oldPassword, user.passwordHash):
            raise ApiError("ADMIN_INVALID_CREDENTIALS", httpStatus=401)
        changePassword(db, userId=userId, newPassword=payload.newPassword)
        return successEnvelope(AdminChangePasswordResponse(success=True).model_dump())


@bp.get("/health")
def adminHealth():
    """管理后台健康检查(无需鉴权)。"""
    payload, code = buildHealthPayload()
    return successEnvelope(payload, httpStatus=code)


__all__ = ["bp"]
