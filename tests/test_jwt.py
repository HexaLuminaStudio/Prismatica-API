# coding: utf-8
"""JWT 编/解码测试。"""
from __future__ import annotations

import time

import jwt as pyjwt

from app.security.jwt import decodeAccessToken, encodeAccessToken


def test_encode_decode_roundtrip():
    token, ttl = encodeAccessToken(userId="user-123", deviceId="dev-456", tier="beta")
    assert ttl > 0
    payload = decodeAccessToken(token)
    assert payload["sub"] == "user-123"
    assert payload["did"] == "dev-456"
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