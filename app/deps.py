"""deps.py — Depends / 装饰器:鉴权 / db session / admin token / admin cookie。"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Any

import jwt as pyjwt
from flask import g, request
from sqlalchemy import select

from app.config import getSettings
from app.db import getDb
from app.errors import ApiError
from app.middleware.admin_session import readSessionCookie
from app.models import AdminUser, IdentityDevice, RevokedToken
from app.security.jwt import decodeAccessToken

_settings = getSettings()


@dataclass(frozen=True)
class UserAuthContext:
    userId: int
    deviceId: str
    tier: str
    jti: str
    claims: dict[str, Any]


def authenticateUserToken(
    token: str,
    requestDeviceId: str,
    db,
) -> UserAuthContext:
    """校验新用户 Access Token、设备绑对和 jti 吊销状态。"""
    try:
        payload = decodeAccessToken(token)
    except pyjwt.ExpiredSignatureError as error:
        raise ApiError("UNAUTHORIZED", "登录已过期", httpStatus=401) from error
    except pyjwt.InvalidTokenError as error:
        raise ApiError("UNAUTHORIZED", "无效登录凭证", httpStatus=401) from error

    claimedDeviceId = payload.get("device_id")
    if not requestDeviceId:
        raise ApiError("UNAUTHORIZED", "缺少 X-Device-Id", httpStatus=401)
    if not claimedDeviceId or requestDeviceId != claimedDeviceId:
        raise ApiError("UNAUTHORIZED", "设备与登录凭证不匹配", httpStatus=401)

    subject = payload.get("sub")
    jti = payload.get("jti")
    if not isinstance(subject, str) or not subject.isdecimal() or not jti:
        raise ApiError("UNAUTHORIZED", "登录凭证 claims 不完整", httpStatus=401)
    if db.get(RevokedToken, jti) is not None:
        raise ApiError("TOKEN_REVOKED", httpStatus=401)

    activeDevice = db.execute(
        select(IdentityDevice).where(
            IdentityDevice.userId == int(subject),
            IdentityDevice.deviceId == claimedDeviceId,
            IdentityDevice.status == "active",
        )
    ).scalar_one_or_none()
    if activeDevice is None:
        raise ApiError(
            "TOKEN_REVOKED",
            "该设备的登录已被撤销，请重新登录",
            httpStatus=401,
        )

    return UserAuthContext(
        userId=int(subject),
        deviceId=claimedDeviceId,
        tier=str(payload.get("tier", "free")),
        jti=str(jti),
        claims=payload,
    )


def requireUser(func):
    """P0-A 用户鉴权：Bearer + X-Device-Id + jti blacklist。"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError("UNAUTHORIZED", "缺少 Authorization 头", httpStatus=401)
        token = auth[len("Bearer ") :].strip()
        with getDb() as db:
            context = authenticateUserToken(
                token,
                request.headers.get("X-Device-Id", "").strip(),
                db,
            )
        g.userId = context.userId
        g.deviceId = context.deviceId
        g.tier = context.tier
        g.jti = context.jti
        g.authClaims = context.claims
        return func(*args, **kwargs)

    return wrapper


require_user = requireUser


def _naiveUtcToEpoch(dt) -> int:
    """把 naive UTC datetime 转为 epoch 秒(避免依赖本地时区)。

    DB 列 DATETIME(3) 是 naive 且 MySQL session 时区一般是 UTC;
    直接 .timestamp() 会按本地时区解释,在不同时区的环境里算出错误值,
    甚至在早于 1970 的日期触发 OSError / ValueError → 500 INTERNAL_ERROR。
    """
    from datetime import UTC

    return int(dt.replace(tzinfo=UTC).timestamp())


def requireAuth(func):
    """装饰器:从 Authorization: Bearer <jwt> 解析 userId,放入 flask.g.userId。"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise ApiError("UNAUTHORIZED", "缺少 Authorization 头", httpStatus=401)
        token = auth[len("Bearer ") :].strip()
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
                # 密码已重置?旧 cookie 失效(cookieTs < pwdResetAt epoch)
                # DB 存的是 naive UTC,必须显式补 UTC tzinfo 后再 .timestamp(),
                # 否则会按进程本地时区解释,容器/Windows 跨时区会出现"cookie 永远失效"
                # 甚至负 epoch → OverflowError / OSError → 500 INTERNAL_ERROR。
                if pwdResetAt is not None and cookieTs is not None and int(cookieTs) < _naiveUtcToEpoch(pwdResetAt):
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
    "UserAuthContext",
    "authenticateUserToken",
    "requireUser",
    "require_user",
    "requireAuth",
    "requireAdmin",
    "requireAdminCookie",
    "requireOwner",
    "getClientIp",
]
