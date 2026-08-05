"""HMAC 工具测试(无需 DB)。"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

from app.config import getSettings
from app.models.license_models import InviteCode, RechargeCode, TrialCode, UserTier
from app.security import hmac as hmacUtil

_settings = getSettings()


def _encode(model) -> str:
    """签发一份凭证(与客户端 signed_code.encodeSignedModel 同款)。"""
    payload = model.model_dump(mode="json")
    payload["signature"] = hmacUtil.signPayload(payload)
    return base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


def test_invite_roundtrip():
    inv = InviteCode(
        code="INV-AAAA-BBBB-CCCC-DDDD",
        grantedBalance=100,
        grantedDays=30,
        tier=UserTier.BETA,
        expireAt=datetime.utcnow() + timedelta(days=14),
    )
    raw = _encode(inv)
    decoded = hmacUtil.decodeSignedCode(raw)
    assert "signature" in decoded
    assert hmacUtil.verifyPayload(
        {k: v for k, v in decoded.items() if k != "signature"},
        decoded["signature"],
    )


def test_trial_roundtrip():
    inv = TrialCode(
        code="TRY-AAAA-BBBB-CCCC-DDDD",
        grantedBalance=20,
        grantedDays=7,
        tier=UserTier.TRIAL,
        expireAt=datetime.utcnow() + timedelta(days=14),
    )
    raw = _encode(inv)
    decoded = hmacUtil.decodeSignedCode(raw)
    assert hmacUtil.verifyPayload(
        {k: v for k, v in decoded.items() if k != "signature"},
        decoded["signature"],
    )


def test_recharge_roundtrip():
    inv = RechargeCode(
        code="RCH-AAAA-BBBB-CCCC-DDDD",
        amount=50,
        expireAt=datetime.utcnow() + timedelta(days=30),
    )
    raw = _encode(inv)
    decoded = hmacUtil.decodeSignedCode(raw)
    assert hmacUtil.verifyPayload(
        {k: v for k, v in decoded.items() if k != "signature"},
        decoded["signature"],
    )


def test_tampered_signature_rejected():
    inv = InviteCode(
        code="INV-AAAA-BBBB-CCCC-DDDD",
        grantedBalance=100,
        grantedDays=30,
        tier=UserTier.BETA,
        expireAt=datetime.utcnow() + timedelta(days=14),
    )
    raw = _encode(inv)
    decoded = hmacUtil.decodeSignedCode(raw)
    # 篡改 grantedBalance 后验签应失败
    tampered = dict(decoded)
    tampered["grantedBalance"] = 99999
    payload = {k: v for k, v in tampered.items() if k != "signature"}
    assert hmacUtil.verifyPayload(payload, decoded["signature"]) is False


def test_garbage_code_raises():
    import pytest

    with pytest.raises(ValueError):
        hmacUtil.decodeSignedCode("not-base64-at-all-!!!")


def test_hash_code_idempotent():
    assert hmacUtil.hashCode("RCH-AAA") == hmacUtil.hashCode("RCH-AAA")
    assert hmacUtil.hashCode("RCH-AAA") != hmacUtil.hashCode("RCH-BBB")
