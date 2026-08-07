"""P0-A JWT 签发与校验（HS256 + jti + device_id）。"""

from __future__ import annotations

import time
from typing import Any, Literal
from uuid import uuid4

import jwt

from app.config import getSettings

_settings = getSettings()
TokenType = Literal["access", "refresh"]


def _createToken(
    userId: int | str,
    deviceId: str,
    tokenType: TokenType,
    jti: str | None,
    expiresIn: int,
    tier: str | None = None,
) -> str:
    if not str(userId):
        raise ValueError("user_id 不能为空")
    if not deviceId:
        raise ValueError("device_id 不能为空")
    if expiresIn <= 0:
        raise ValueError("token TTL 必须大于 0")

    now = int(time.time())
    tokenJti = jti or str(uuid4())
    payload: dict[str, Any] = {
        "iss": _settings.jwtIssuer,
        "aud": _settings.jwtAudience,
        "sub": str(userId),
        "device_id": deviceId,
        "did": deviceId,  # 旧桌面端过渡 alias，M3 客户端切换后删除
        "jti": tokenJti,
        "token_type": tokenType,
        "iat": now,
        "exp": now + expiresIn,
        "ver": "v3",
    }
    if tier is not None:
        payload["tier"] = tier
    return jwt.encode(payload, _settings.jwtSecret, algorithm=_settings.jwtAlg)


def createAccessToken(
    userId: int | str,
    deviceId: str,
    tier: str,
    jti: str | None = None,
) -> str:
    """创建短期 Access Token。"""
    return _createToken(
        userId,
        deviceId,
        "access",
        jti,
        _settings.jwtAccessTtlSec,
        tier=tier,
    )


def createRefreshToken(
    userId: int | str,
    deviceId: str,
    jti: str | None = None,
) -> str:
    """创建长期 Refresh Token；持久化由 auth service 在同一事务完成。"""
    return _createToken(
        userId,
        deviceId,
        "refresh",
        jti,
        _settings.jwtRefreshTtlSec,
    )


def _decodeToken(token: str, expectedType: TokenType) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        _settings.jwtSecret,
        algorithms=[_settings.jwtAlg],
        audience=_settings.jwtAudience,
        issuer=_settings.jwtIssuer,
        options={"require": ["sub", "device_id", "jti", "iat", "exp"]},
    )
    if payload.get("token_type") != expectedType:
        raise jwt.InvalidTokenError(f"expected {expectedType} token")
    return payload


def decodeAccessToken(token: str) -> dict[str, Any]:
    """解码 Access Token；过期或 claims 不完整时抛 PyJWTError。"""
    return _decodeToken(token, "access")


def decodeRefreshToken(token: str) -> dict[str, Any]:
    """解码 Refresh Token；不接受 Access Token 混用。"""
    return _decodeToken(token, "refresh")


def encodeAccessToken(
    userId: int | str,
    deviceId: str,
    tier: str = "free",
) -> tuple[str, int]:
    """旧调用方兼容包装；M3 完成后统一改用 createAccessToken。"""
    token = createAccessToken(userId, deviceId, tier)
    return token, _settings.jwtAccessTtlSec


# P0-A 计划中的 snake_case 公共 API。
create_access_token = createAccessToken
create_refresh_token = createRefreshToken
decode_access_token = decodeAccessToken
decode_refresh_token = decodeRefreshToken

__all__ = [
    "createAccessToken",
    "createRefreshToken",
    "decodeAccessToken",
    "decodeRefreshToken",
    "encodeAccessToken",
    "create_access_token",
    "create_refresh_token",
    "decode_access_token",
    "decode_refresh_token",
]
