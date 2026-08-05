"""deps.py — Depends / 装饰器:鉴权 / db session / admin token / admin cookie。
"""

from __future__ import annotations

from functools import wraps

import jwt as pyjwt
from flask import g, request

from app.config import getSettings
from app.errors import ApiError
from app.middleware.admin_session import readSessionCookie
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
    成功时把 admin userId / username / role 放入 flask.g:
        - g.adminUserId
        - g.adminUsername
        - g.adminRole
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        payload = readSessionCookie()
        if payload is not None:
            userId = payload.get("userId")
            username = payload.get("username")
        else:
            # 降级到 X-Admin-Token(仅当 settings.adminToken 有效时)
            token = request.headers.get("X-Admin-Token", "")
            if token and token == _settings.adminToken:
                userId = "cli-admin"
                username = "cli-admin"
            else:
                raise ApiError("ADMIN_LOGIN_REQUIRED", httpStatus=401)

        g.adminUserId = userId
        g.adminUsername = username
        g.adminActor = username or "admin"
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
    "getClientIp",
]
