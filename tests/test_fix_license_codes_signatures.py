"""scripts/fix_license_codes_signatures.py 的回归测试(2026-08-06)。

不连真实 DB;直接验证:
    - _tryParseSigned 正确识别合法 / 非法 payload
    - _rebuildSignedPayload 产生的 base64 能被后端 decodeSignedCode 反解
    - sha256(codeBody) == codeHash 一致性校验
"""

from __future__ import annotations

from datetime import datetime

from app.security.hmac import hashCode
from scripts.fix_license_codes_signatures import (
    _rebuildSignedPayload,
    _tryParseSigned,
)


def _buildSampleSigned() -> str:
    """构造一个合法的 signed payload(等价 admin_code_service 内部行为)。"""
    return _rebuildSignedPayload(
        codeBody="INV-AB12-CD34-EF56-GH78",
        kind="invite",
        grantedBalance=100,
        grantedDays=30,
        tier="beta",
        amount=None,
        expireAt=datetime(2026, 12, 31, 23, 59, 59),
    )


def test_try_parse_accepts_valid_signed():
    signed = _buildSampleSigned()
    ok, info = _tryParseSigned(signed)
    assert ok is True, info
    assert info == "ok"


def test_try_parse_rejects_none():
    ok, info = _tryParseSigned(None)
    assert ok is False
    assert "NULL" in info


def test_try_parse_rejects_empty():
    ok, info = _tryParseSigned("")
    assert ok is False


def test_try_parse_rejects_invalid_base64_padding():
    # 长度非 4 的倍数且 padding 不全 → Incorrect padding
    ok, info = _tryParseSigned("abc")
    assert ok is False
    assert "padding" in info.lower() or "binascii" in info.lower()


def test_try_parse_rejects_non_dict_payload():
    # 合法 base64 但内容是 array
    import base64

    payload = base64.b64encode(b'["a","b"]').decode("ascii")
    ok, info = _tryParseSigned(payload)
    assert ok is False
    assert "not a dict" in info


def test_try_parse_rejects_missing_signature():
    import base64

    payload = base64.b64encode(b'{"code":"INV-X"}').decode("ascii")
    ok, info = _tryParseSigned(payload)
    assert ok is False
    assert "signature" in info


def test_try_parse_rejects_missing_code_field():
    import base64

    payload = base64.b64encode(b'{"signature":"abc","grantedBalance":100}').decode("ascii")
    ok, info = _tryParseSigned(payload)
    assert ok is False
    assert "'code' field" in info


def test_rebuild_roundtrip_keeps_sha256_consistency():
    """sha256(codeBody) 必须等于 license_codes.codeHash 的来源。"""
    codeBody = "INV-XYZA-BCDE-FGHI-JKLM"
    expected = hashCode(codeBody)
    signed = _rebuildSignedPayload(
        codeBody=codeBody,
        kind="invite",
        grantedBalance=50,
        grantedDays=14,
        tier="trial",
        amount=None,
        expireAt=datetime(2026, 12, 31),
    )
    # signed 中应包含 codeBody,服务端 redeem 时会用同一 codeBody 重新算 sha256 查表
    import base64
    import json

    decoded = json.loads(base64.b64decode(signed).decode("utf-8"))
    assert decoded["code"] == codeBody
    assert hashCode(decoded["code"]) == expected


def test_rebuild_invite_payload_has_invite_fields():
    import base64
    import json

    signed = _rebuildSignedPayload(
        codeBody="INV-AAAA-BBBB-CCCC-EEEE",
        kind="invite",
        grantedBalance=200,
        grantedDays=60,
        tier="beta_pro",
        amount=None,
        expireAt=datetime(2027, 1, 1),
    )
    data = json.loads(base64.b64decode(signed).decode("utf-8"))
    assert data["code"].startswith("INV-")
    assert data["grantedBalance"] == 200
    assert data["grantedDays"] == 60
    assert data["tier"] == "beta_pro"
    assert data["maxUses"] == 1
    assert "signature" in data


def test_rebuild_trial_payload_uses_trial_tier_constant():
    import base64
    import json

    signed = _rebuildSignedPayload(
        codeBody="TRY-AAAA-BBBB-CCCC-DDDD",
        kind="trial",
        grantedBalance=20,
        grantedDays=7,
        tier=None,
        amount=None,
        expireAt=datetime(2027, 1, 1),
    )
    data = json.loads(base64.b64decode(signed).decode("utf-8"))
    # 即便调用方传 None,也应该规范化成 TRIAL(对齐 admin_code_service 行为)
    assert data["tier"] == "trial"


def test_rebuild_recharge_payload_has_amount_and_note():
    import base64
    import json

    signed = _rebuildSignedPayload(
        codeBody="RCH-AAAA-BBBB-CCCC-FFFF",
        kind="recharge",
        grantedBalance=None,
        grantedDays=None,
        tier=None,
        amount=500,
        expireAt=datetime(2027, 6, 1),
    )
    data = json.loads(base64.b64decode(signed).decode("utf-8"))
    assert data["amount"] == 500
    assert data["note"] == "admin issued"
