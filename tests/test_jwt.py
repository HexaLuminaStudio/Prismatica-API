"""JWT 编/解码测试。"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest

from app.security.jwt import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    decodeAccessToken,
    encodeAccessToken,
)


def test_encode_decode_roundtrip():
    token, ttl = encodeAccessToken(userId="user-123", deviceId="dev-456", tier="beta")
    assert ttl > 0
    payload = decodeAccessToken(token)
    assert payload["sub"] == "user-123"
    assert payload["did"] == "dev-456"
    assert payload["device_id"] == "dev-456"
    assert payload["jti"]
    assert payload["token_type"] == "access"
    assert payload["tier"] == "beta"
    assert payload["iss"] == "prismatica-api"
    assert payload["aud"] == "prismatica-client"


def test_expired_token_rejected():
    import pytest

    from app.config import getSettings

    settings = getSettings()
    # 临时把 TTL 改成 1 秒
    oldTtl = settings.jwtAccessTtlSec
    settings.jwtAccessTtlSec = 1
    try:
        token, _ = encodeAccessToken(userId="u", deviceId="d")
        time.sleep(2)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            decodeAccessToken(token)
    finally:
        settings.jwtAccessTtlSec = oldTtl


def test_invalid_token_rejected():
    import pytest

    with pytest.raises(pyjwt.InvalidTokenError):
        decodeAccessToken("garbage.token.value")


def test_explicit_jti_is_preserved():
    token = create_access_token(
        42,
        "device-42",
        "pro",
        "fixed-jti",
        authVersion=7,
    )

    payload = decodeAccessToken(token)

    assert payload["sub"] == "42"
    assert payload["jti"] == "fixed-jti"
    assert payload["tier"] == "pro"
    assert payload["auth_version"] == 7


def test_refresh_token_has_independent_type_and_jti():
    access = create_access_token(42, "device-42", "free")
    refresh = create_refresh_token(42, "device-42")

    accessPayload = decodeAccessToken(access)
    refreshPayload = decode_refresh_token(refresh)

    assert refreshPayload["token_type"] == "refresh"
    assert refreshPayload["jti"] != accessPayload["jti"]
    with pytest.raises(pyjwt.InvalidTokenError):
        decodeAccessToken(refresh)
