"""凭证兑换核心语义测试(内存 SQLite,不依赖 MySQL)。

覆盖 B1/B2 修复点:
    - 同码二次兑换 → 同一 userId 且余额不重复发放(幂等恢复)
    - 同设备换新码 → 同一 userId、余额合并
    - 激活码兑换 → 建用户、expireAt = validityPeriod 当日 23:59:59、不发余额
    - 新用户 userId 为 BIGINT 主键
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CodeRedemption, LicenseCode, UserAccount, UserBalance
from app.models.identity import IdentityDevice
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


def test_redeem_new_code_creates_bigint_user(db):
    """全新设备 + 全新凭证 → 新建 BIGINT 用户。"""
    code = _inviteCode()
    r = redeemCode(
        db,
        code,
        "00000000-0000-0000-0000-000000000001",
        deviceName="pc",
        platform="win",
    )
    assert r.mode == "invite"
    assert r.user.userId.isdecimal()
    assert r.balance.balance == 100
    # 设备已绑定(P0-A IdentityDevice 主键是 BIGINT id,按 deviceId 字段查)
    device = db.execute(
        select(IdentityDevice).where(IdentityDevice.deviceId == "00000000-0000-0000-0000-000000000001")
    ).scalar_one_or_none()
    assert device is not None
    assert device.userId == int(r.user.userId)
    # 幂等表已消费
    seen = db.execute(
        select(LicenseCode).where(LicenseCode.codeHash == hmacUtil.hashCode("INV-TEST-0001-0001"))
    ).scalar_one()
    assert seen is not None
    assert seen.status == "exhausted"
    redemption = db.execute(select(CodeRedemption).where(CodeRedemption.codeId == seen.id)).scalar_one()
    assert redemption.userId == int(r.user.userId)


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
    seen = db.execute(
        select(LicenseCode).where(LicenseCode.codeHash == hmacUtil.hashCode("INV-TEST-0001-0001"))
    ).scalar_one()
    assert seen.status == "exhausted"
    assert seen.usedCount == 1


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
    # 2026-08-07:IdentityUser 主键是 BIGINT id;UserAccount alias 共用同一表;
    # 按 userId(String) 查需要 select 表达式,而不是 db.get。
    balance = db.execute(select(UserBalance).where(UserBalance.userId == r1.user.userId)).scalar_one_or_none()
    assert balance is not None
    assert balance.balance == 200
    user = db.execute(select(UserAccount).where(UserAccount.id == int(r1.user.userId))).scalar_one_or_none()
    assert user is not None


def test_activation_code_sets_expire_at_from_validity(db):
    """激活码兑换 → 建用户、expireAt = validityPeriod 当日 23:59:59、不发余额。"""
    code = _activationCode(validityDays=90)
    deviceId = "00000000-0000-0000-0000-000000000003"

    r = redeemCode(db, code, deviceId)
    assert r.mode == "activation"
    assert r.user.tier == "pro"  # 存量档位收敛到 canonical tier
    # expireAt 取码有效期当日 23:59:59
    validity = (datetime.utcnow() + timedelta(days=90)).strftime("%Y-%m-%d")
    expected = datetime.strptime(validity, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
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


# ---------------------------------------------------------------------------
# 2026-08-06:明文 INV/TRY/RCH 码兑换(admin 后台下发给用户的明文形式)
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_with_getdb(db):
    """明文码与兑换逻辑共用同一事务会话。"""
    return db


def test_plaintext_invite_code_redemption(db_with_getdb):
    """明文 INV-XXX 码 → 通过 license_codes 表反查 signed payload 完成兑换。"""

    # 1) admin 签发(等价 AdminCodeService.issueCodes):写 license_codes + 生成 signedPayload
    plainCode = "INV-PLAIN-AAAA-BBBB-CCCC"
    row = LicenseCode(
        codeHash=hmacUtil.hashCode(plainCode),
        codeKind="INV",
        status="active",
        planCode="pro",
        periodMonths=1,
        monthlyQuota=100,
        maxUses=1,
        usedCount=0,
        expiresAt=datetime.utcnow() + timedelta(days=30),
    )
    db_with_getdb.add(row)
    db_with_getdb.commit()

    # 2) 用户粘贴明文码 → redeem 应成功(走 fallback 路径)
    deviceId = "00000000-0000-0000-0000-00000000fff1"
    r = redeemCode(db_with_getdb, plainCode, deviceId)

    assert r.mode == "invite"
    assert r.balance.balance == 100
    assert r.user.tier == "pro"
    # 幂等表已消费
    seen = db_with_getdb.execute(
        select(LicenseCode).where(LicenseCode.codeHash == hmacUtil.hashCode(plainCode))
    ).scalar_one()
    assert seen.status == "exhausted"


def test_plaintext_code_not_in_db_rejected(db_with_getdb):
    """明文 INV-XXX 码但 license_codes 表查不到 → INVALID_CODE。"""
    from app.errors import ApiError

    bogusCode = "INV-XXXX-YYYY-ZZZZ-0000"
    deviceId = "00000000-0000-0000-0000-00000000fff2"
    with pytest.raises(ApiError) as ei:
        redeemCode(db_with_getdb, bogusCode, deviceId)
    assert ei.value.code == "INVALID_CODE"


def test_plaintext_code_uses_database_metadata(db_with_getdb):
    """明文码只依赖 canonical license_codes 元数据。"""
    plainCode = "INV-NOSIG-AAAA-BBBB-CCCC"
    row = LicenseCode(
        codeHash=hmacUtil.hashCode(plainCode),
        codeKind="INV",
        status="active",
        planCode="pro",
        periodMonths=1,
        monthlyQuota=100,
        maxUses=1,
        usedCount=0,
        expiresAt=datetime.utcnow() + timedelta(days=30),
    )
    db_with_getdb.add(row)
    db_with_getdb.commit()

    deviceId = "00000000-0000-0000-0000-00000000fff3"
    r = redeemCode(db_with_getdb, plainCode, deviceId)
    assert r.mode == "invite"
    assert r.balance.balance == 100


def test_plaintext_code_kind_mapping_succeeds(db_with_getdb):
    """canonical INV 类型可还原为公开 invite 语义。"""
    plainCode = "INV-CORRUPT-AAAA-BBBB"
    row = LicenseCode(
        codeHash=hmacUtil.hashCode(plainCode),
        codeKind="INV",
        status="active",
        planCode="pro",
        periodMonths=1,
        monthlyQuota=100,
        maxUses=1,
        usedCount=0,
        expiresAt=datetime.utcnow() + timedelta(days=30),
    )
    db_with_getdb.add(row)
    db_with_getdb.commit()

    deviceId = "00000000-0000-0000-0000-00000000fff4"
    r = redeemCode(db_with_getdb, plainCode, deviceId)
    assert r.mode == "invite"
    assert r.balance.balance == 100


def test_plaintext_code_missing_expire_rejected(db_with_getdb):
    """license_codes 元数据不完整(缺 expireAt)→ INVALID_CODE。"""
    from app.errors import ApiError

    plainCode = "INV-NOEXP-AAAA-BBBB-CCCC"
    row = LicenseCode(
        codeHash=hmacUtil.hashCode(plainCode),
        codeKind="INV",
        status="active",
        planCode="pro",
        periodMonths=1,
        monthlyQuota=100,
        maxUses=1,
        usedCount=0,
        expiresAt=None,  # 关键:元数据缺失
    )
    db_with_getdb.add(row)
    db_with_getdb.commit()

    with pytest.raises(ApiError) as ei:
        redeemCode(db_with_getdb, plainCode, "00000000-0000-0000-0000-00000000fff5")
    assert ei.value.code == "INVALID_CODE"
    assert "有效期" in ei.value.message
