"""deps.py — Depends / 装饰器:鉴权 / db session / admin token / admin cookie。
"""

from __future__ import annotations

from functools import wraps

import jwt as pyjwt
from flask import g, request

from app.config import getSettings
from app.db import getDb
from app.errors import ApiError
from app.middleware.admin_session import readSessionCookie
from app.models import AdminUser
from app.security.jwt import decodeAccessToken

_settings = getSettings()


def requireAuth(func):
    """装饰器:从 Authorization: Bearer <jwt> 解析 userId,放入 flask.g.userId。"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError("UNAUTHORIZED", "缺少 Authorization 头", httpStatus=401)
        token = auth[len("Bearer "):].strip()
        try:
            payload = decodeAccessToken(token)
        except pyjwt.ExpiredSignatureError as e:
            raise ApiError("UNAUTHORIZED", "登录已过期", httpStatus=401) from e
        except pyjwt.InvalidTokenError as e:
            raise ApiError("UNAUTHORIZED", f"无效 token: {e}", httpStatus=401) from e

        userId: str | None = payload.get("sub")
        deviceId: str | None = payload.get("did")
        if not userId or not deviceId:
            raise ApiError("UNAUTHORIZED", "token 缺少 sub/did", httpStatus=401)

        g.userId = userId
        g.deviceId = deviceId
        g.tier = payload.get("tier", "beta")
        return func(*args, **kwargs)

    return wrapper


def requireAdmin(func):
    """装饰器:校验 X-Admin-Token(供运营 CLI / curl 仍可用)。"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-Admin-Token", "")
        if not token or token != _settings.adminToken:
            raise ApiError("FORBIDDEN", "权限不足", httpStatus=403)
        g.adminActor = "admin"
        return func(*args, **kwargs)

    return wrapper


def requireAdminCookie(func):
    """装饰器:既接受 cookie session(浏览器),也接受 X-Admin-Token(curl/脚本)。

    优先级:cookie > header。
    成功时把 admin userId / username / role / pwd_reset_at 放入 flask.g:
        - g.adminUserId
        - g.adminUsername
        - g.adminRole
        - g.adminPwdResetAt(若非 None 且 cookie.ts < 该值 → 401)

    注意:role / pwdResetAt 始终从 DB 读最新值,不允许 cookie 篡改。
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        payload = readSessionCookie()
        cookieTs: int | None = None
        if payload is not None:
            userId = payload.get("userId")
            username = payload.get("username")
            cookieTs = payload.get("ts")
        else:
            # 降级到 X-Admin-Token(仅当 settings.adminToken 有效时)
            token = request.headers.get("X-Admin-Token", "")
            if token and token == _settings.adminToken:
                userId = "cli-admin"
                username = "cli-admin"
            else:
                raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)

        # cookie 路径:从 DB 读 role / pwdResetAt(防篡改)
        if userId == "cli-admin":
            role = "owner"  # X-Admin-Token 默认 owner,运营后台信任
            pwdResetAt = None
        else:
            with getDb() as db:
                row = db.get(AdminUser, userId)
                if row is None or row.deletedAt is not None:
                    raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)
                role = row.role
                pwdResetAt = row.pwdResetAt
                # 密码已重置?旧 cookie 失效(cookieTs < pwdResetAt)
                if (
                    pwdResetAt is not None
                    and cookieTs is not None
                    and int(cookieTs) < int(pwdResetAt.timestamp())
                ):
                    raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)
                # locked 状态拒绝访问
                if row.status == "locked":
                    raise ApiError("ADMIN_ACCOUNT_LOCKED", httpStatus=423)

        g.adminUserId = userId
        g.adminUsername = username
        g.adminRole = role
        g.adminPwdResetAt = pwdResetAt
        g.adminActor = username or "admin"
        return func(*args, **kwargs)

    return wrapper


def requireOwner(func):
    """装饰器:仅 owner 可访问(必须先经过 requireAdminCookie)。

    使用:
        @bp.post("/...")
        @requireAdminCookie
        @requireOwner
        def handler(): ...
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        if getattr(g, "adminRole", None) != "owner":
            raise ApiError("FORBIDDEN", "仅 owner 可操作", httpStatus=403)
        return func(*args, **kwargs)

    return wrapper


def getClientIp() -> str | None:
    """客户端 IP(优先 X-Forwarded-For,否则 remote_addr)。"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


__all__ = [
    "requireAuth",
    "requireAdmin",
    "requireAdminCookie",
    "requireOwner",
    "getClientIp",
]
