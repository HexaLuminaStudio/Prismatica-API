"""/admin/* 路由(2026-08-05 M2 B1):login / logout / me / change-password。"""

from __future__ import annotations

from contextlib import contextmanager

from flask import Blueprint, g, jsonify, make_response, request
from pydantic import ValidationError

from app.db import getDb
from app.deps import getClientIp, requireAdminCookie
from app.errors import ApiError
from app.middleware.admin_session import clearSessionCookie, setSessionCookie
from app.models import AdminUser
from app.schemas.admin_auth import (
    AdminChangePasswordRequest,
    AdminLoginRequest,
    AdminMeResponse,
)
from app.services.admin_auth_service import changePassword, loginByPassword

bp = Blueprint("admin_auth", __name__, url_prefix="/admin")


@contextmanager
def _sessionCtx():
    with getDb() as db:
        yield db


@bp.post("/login")
def postLogin():
    """用户名密码登录 → 颁 cookie(失败不颁)。

    错误:
        - 401 ADMIN_INVALID_CREDENTIALS
        - 423 ADMIN_ACCOUNT_LOCKED
    """
    try:
        payload = AdminLoginRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    with _sessionCtx() as db:
        user = loginByPassword(
            db,
            username=payload.username,
            password=payload.password,
            ip=getClientIp(),
        )
        resp = make_response(
            jsonify(
                {
                    "userId": user.userId,
                    "username": user.username,
                    "role": user.role,
                    "status": user.status,
                }
            )
        )
        setSessionCookie(resp, userId=user.userId, username=user.username)
        resp.status_code = 200
        return resp


@bp.post("/logout")
def postLogout():
    """清除 cookie(无需鉴权:无 cookie 也是 204)。"""
    resp = make_response("", 204)
    clearSessionCookie(resp)
    return resp


@bp.get("/me")
@requireAdminCookie
def getMe():
    """返回当前登录管理员信息。"""
    userId = getattr(g, "adminUserId", "")
    if not userId or userId == "cli-admin":
        # CLI mode:仅返回占位信息,不查 DB
        return jsonify(
            {
                "userId": "cli-admin",
                "username": "cli-admin",
                "role": "admin",
                "status": "active",
            }
        )
    with _sessionCtx() as db:
        user = db.get(AdminUser, userId)
        if user is None:
            raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)
        out = AdminMeResponse(
            userId=user.userId,
            username=user.username,
            role=user.role,
            status=user.status,
            lastLoginAt=user.lastLoginAt,
        )
        return jsonify(out.model_dump(mode="json"))


@bp.post("/me/change-password")
@requireAdminCookie
def postChangePassword():
    """修改自身密码(强制要求 ≥ 8 位)。"""
    try:
        payload = AdminChangePasswordRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    userId = getattr(g, "adminUserId", "")
    if not userId or userId == "cli-admin":
        raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)

    with _sessionCtx() as db:
        # 验证旧密码
        user = db.get(AdminUser, userId)
        if user is None:
            raise ApiError("NOT_FOUND", "管理员账号不存在")
        from app.security.password import verifyPassword

        if not verifyPassword(payload.oldPassword, user.passwordHash):
            raise ApiError("ADMIN_INVALID_CREDENTIALS", httpStatus=401)
        changePassword(db, userId=userId, newPassword=payload.newPassword)
        return jsonify({"success": True})


@bp.get("/health")
def adminHealth():
    """管理后台健康检查(无需鉴权)。"""
    return jsonify({"status": "ok", "scope": "admin"})
