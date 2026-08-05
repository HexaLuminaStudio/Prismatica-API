"""凭证兑换核心语义测试(内存 SQLite,不依赖 MySQL)。

覆盖 B1/B2 修复点:
    - 同码二次兑换 → 同一 userId 且余额不重复发放(幂等恢复)
    - 同设备换新码 → 同一 userId、余额合并
    - 激活码兑换 → 建用户、expireAt = validityPeriod 当日 23:59:59、不发余额
    - 新用户 userId 为合法 UUID(v4) 格式
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import LicenseCodeSeen, UserAccount, UserBalance, UserDevice
from app.security import hmac as hmacUtil
from app.services.auth_service import redeemCode


@pytest.fixture()
def db():
    """内存 SQLite 会话(每次测试隔离)。"""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 工具:构造合法签名凭证(与客户端 signed_code.py 格式一致)
# ---------------------------------------------------------------------------


def _sign(payload: dict) -> str:
    """payload → base64(JSON{payload, signature})。"""
    payload = dict(payload)
    payload["signature"] = hmacUtil.signPayload(payload)
    raw = json.dumps(payload, ensure_ascii=False)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def _inviteCode(**overrides) -> str:
    payload = {
        "code": "INV-TEST-0001-0001",
        "maxUses": 1,
        "grantedBalance": 100,
        "grantedDays": 30,
        "tier": "beta",
        "expireAt": (datetime.utcnow() + timedelta(days=30)).isoformat(),
        "issuedAt": datetime.utcnow().isoformat(),
        "version": 1,
    }
    payload.update(overrides)
    return _sign(payload)


def _activationCode(validityDays: int = 90, userType: str = "正式用户") -> str:
    validity = (datetime.utcnow() + timedelta(days=validityDays)).strftime("%Y-%m-%d")
    payload = {
        "deviceCode": "legacy-fp-xxxx",
        "validityPeriod": validity,
        "userType": userType,
        "issuedAt": datetime.utcnow().isoformat(),
        "version": 1,
    }
    return _sign(payload)


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_redeem_new_code_creates_uuid4_user(db):
    """全新设备 + 全新凭证 → 新建用户,userId 为合法 UUID v4。"""
    code = _inviteCode()
    r = redeemCode(
        db, code, "00000000-0000-0000-0000-000000000001",
        deviceName="pc", platform="win",
    )
    assert r.mode == "invite"
    parsed = uuid.UUID(r.user.userId)
    assert parsed.version == 4
    assert r.balance.balance == 100
    # 设备已绑定
    device = db.get(UserDevice, "00000000-0000-0000-0000-000000000001")
    assert device is not None
    assert device.userId == r.user.userId
    # 幂等表已消费
    seen = db.get(LicenseCodeSeen, hmacUtil.hashCode(code))
    assert seen is not None
    assert seen.consumedByUserId == r.user.userId


def test_same_code_second_redeem_restores_identity_no_double_grant(db):
    """重输同一凭证(模拟本地凭证丢失)→ 同一 userId,余额不重复发放。"""
    code = _inviteCode()
    deviceA = "00000000-0000-0000-0000-00000000000a"
    deviceB = "00000000-0000-0000-0000-00000000000b"

    r1 = redeemCode(db, code, deviceA)
    r2 = redeemCode(db, code, deviceB)

    assert r2.user.userId == r1.user.userId  # 跨设备恢复同一身份
    assert r2.balance.balance == 100  # 已消费 → 跳过赠予
    # 幂等表消费记录未改变
    seen = db.get(LicenseCodeSeen, hmacUtil.hashCode(code))
    assert seen.consumedByUserId == r1.user.userId
    assert seen.consumedAt is not None


def test_same_device_new_code_keeps_user_and_merges_balance(db):
    """同设备换新码 → 同一 userId,余额合并(100 + 100 = 200)。"""
    code1 = _inviteCode(code="INV-TEST-0001-0001")
    code2 = _inviteCode(code="INV-TEST-0001-0002")
    deviceId = "00000000-0000-0000-0000-000000000002"

    r1 = redeemCode(db, code1, deviceId)
    r2 = redeemCode(db, code2, deviceId)

    assert r2.user.userId == r1.user.userId
    assert r2.balance.balance == 200
    # 数据库侧余额同样为 200
    balance = db.get(UserBalance, r1.user.userId)
    assert balance.balance == 200
    user = db.get(UserAccount, r1.user.userId)
    assert user is not None


def test_activation_code_sets_expire_at_from_validity(db):
    """激活码兑换 → 建用户、expireAt = validityPeriod 当日 23:59:59、不发余额。"""
    code = _activationCode(validityDays=90)
    deviceId = "00000000-0000-0000-0000-000000000003"

    r = redeemCode(db, code, deviceId)
    assert r.mode == "activation"
    assert r.user.tier == "paid"  # 「正式用户」→ paid
    # expireAt 取码有效期当日 23:59:59
    validity = (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%d")
    expected = datetime.strptime(validity, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59
    )
    assert r.user.expireAt == expected
    assert r.balance.balance == 0  # 激活码不发余额


def test_activation_code_second_redeem_idempotent(db):
    """激活码二次兑换(不同设备)→ 同一 userId,expireAt 不变。"""
    code = _activationCode(validityDays=90)
    deviceA = "00000000-0000-0000-0000-00000000000c"
    deviceB = "00000000-0000-0000-0000-00000000000d"

    r1 = redeemCode(db, code, deviceA)
    r2 = redeemCode(db, code, deviceB)

    assert r2.user.userId == r1.user.userId
    assert r2.user.expireAt == r1.user.expireAt
    assert r2.balance.balance == 0
