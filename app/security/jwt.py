"""JWT 编/解码(HS256)。

PRD §7 JWT 字段:
    sub  = userId
    did  = deviceId
    tier = 用户档位
    iat  / exp / iss / aud / ver
"""
from __future__ import annotations

import time
from typing import Any

import jwt

from app.config import getSettings

_settings = getSettings()


def encodeAccessToken(
    userId: str,
    deviceId: str,
    tier: str = "beta",
) -> tuple[str, int]:
    """签发 access token(HS256)。

    Returns:
        (token, expiresInSec)
    """
    now = int(time.time())
    ttl = _settings.jwtAccessTtlSec
    payload: dict[str, Any] = {
        "sub": userId,
        "did": deviceId,
        "tier": tier,
        "iat": now,
        "exp": now + ttl,
        "iss": _settings.jwtIssuer,
        "aud": _settings.jwtAudience,
        "ver": "v2",
    }
    token = jwt.encode(payload, _settings.jwtSecret, algorithm=_settings.jwtAlg)
    return token, ttl


def decodeAccessToken(token: str) -> dict[str, Any]:
    """解码并校验 access token。失败抛 jwt.PyJWTError。"""
    return jwt.decode(
        token,
        _settings.jwtSecret,
        algorithms=[_settings.jwtAlg],
        audience=_settings.jwtAudience,
        issuer=_settings.jwtIssuer,
    )
