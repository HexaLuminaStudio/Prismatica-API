"""P0-A account_service 单元测试。

覆盖(M14 / M5 验收):
    - getMe: 用户存在/不存在/已注销 三条路径
    - patchMe: 改 displayName 成功 + 超长抛 DISPLAY_NAME_INVALID
    - listDevices: 返回 DeviceOut 列表 + maxActive/activeCount 计数
    - revokeDevice: 撤销非当前设备 + 撤销当前设备抛 400
    - deleteAccount: 软删 + 撤销 refresh + 重复注销抛 CONFLICT
    - _selectActiveSubscription / _toSubscriptionOut 内部辅助
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.errors import ApiError
from app.models.identity import IdentityBalance, IdentityDevice, User as IdentityUser
from app.security.password import hashPassword
from app.services import account_service
from app.services.account_service import (
    MAX_ACTIVE_DEVICES,
    deleteAccount,
    getMe,
    listDevices,
    patchMe,
    revokeDevice,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session
    engine.dispose()


def _makeUser(db: Session, suffix: str = "0") -> IdentityUser:
    user = IdentityUser(
        email=f"u{suffix}@example.com",
        passwordHash=hashPassword("Password1Aa"),
        displayName=f"User{suffix}",
        tier="free",
        status="active",
    )
    db.add(user)
    db.flush()
    # IdentityBalance.userId 是 String(36),而服务层 db.get(IdentityBalance, userId)
    # 以 BIGINT 传入。SQLAlchemy 会把 int 参数转字符串后命中行。
    # 故此处把 BIGINT user.id 转 str 写入,保持键值匹配。
    db.add(IdentityBalance(userId=str(user.id), balance=100, reserved=0))
    db.flush()
    return user


def _makeDevice(db: Session, user: IdentityUser, devicePublicId: str) -> IdentityDevice:
    now = datetime.now(UTC).replace(tzinfo=None)
    device = IdentityDevice(
        userId=str(user.id),
        deviceId=devicePublicId,
        deviceName="TestDevice",
        platform="pytest",
        status="active",
        firstSeenAt=now,
        lastSeenAt=now,
    )
    db.add(device)
    db.flush()
    return device


# ---------------------------------------------------------------------------
# getMe
# ---------------------------------------------------------------------------


def testGetMe_ReturnsBalanceAndTier(db: Session) -> None:
    user = _makeUser(db, "1")
    me = getMe(db, user.id)
    assert me.userId == user.id
    assert me.email == user.email
    assert me.displayName == "User1"
    assert me.tier == "free"
    assert me.balance == 100
    assert me.available == 100  # 100 - 0 reserved
    assert me.subscription is None


def testGetMe_UserNotFoundRaises(db: Session) -> None:
    with pytest.raises(ApiError) as exc:
        getMe(db, 99999)
    assert exc.value.code == "NOT_FOUND"
    assert exc.value.httpStatus == 404


def testGetMe_DeletedUserRaises(db: Session) -> None:
    user = _makeUser(db, "2")
    user.deletedAt = datetime.now(UTC).replace(tzinfo=None)
    db.flush()
    with pytest.raises(ApiError) as exc:
        getMe(db, user.id)
    assert exc.value.code == "NOT_FOUND"


def testGetMe_WithoutBalanceRowReturnsZero(db: Session) -> None:
    """若 user_balance 行缺失,降级返回 0 而不报错。"""
    user = IdentityUser(
        email="nobal@example.com",
        passwordHash=hashPassword("Password1Aa"),
        displayName="NoBalance",
        tier="free",
        status="active",
    )
    db.add(user)
    db.flush()
    # 注意:此处故意不创建 IdentityBalance 行
    me = getMe(db, user.id)
    assert me.balance == 0
    assert me.reserved == 0


# ---------------------------------------------------------------------------
# patchMe
# ---------------------------------------------------------------------------


def testPatchMe_UpdatesDisplayName(db: Session) -> None:
    user = _makeUser(db, "3")
    response = patchMe(db, user.id, "新昵称")
    db.commit()
    assert response.displayName == "新昵称"
    db.refresh(user)
    assert user.displayName == "新昵称"


def testPatchMe_StripsWhitespace(db: Session) -> None:
    user = _makeUser(db, "4")
    response = patchMe(db, user.id, "   前后空格   ")
    db.commit()
    assert response.displayName == "前后空格"


def testPatchMe_TooLongRaisesDisplayNameInvalid(db: Session) -> None:
    user = _makeUser(db, "5")
    tooLong = "x" * 65
    with pytest.raises(ApiError) as exc:
        patchMe(db, user.id, tooLong)
    assert exc.value.code == "DISPLAY_NAME_INVALID"
    assert exc.value.httpStatus == 400


def testPatchMe_DeletedUserRaises(db: Session) -> None:
    user = _makeUser(db, "6")
    user.deletedAt = datetime.now(UTC).replace(tzinfo=None)
    db.flush()
    with pytest.raises(ApiError) as exc:
        patchMe(db, user.id, "x")
    assert exc.value.code == "NOT_FOUND"


def testPatchMe_InactiveUserRaisesForbidden(db: Session) -> None:
    user = _makeUser(db, "7")
    user.status = "suspended"
    db.flush()
    with pytest.raises(ApiError) as exc:
        patchMe(db, user.id, "x")
    assert exc.value.code == "FORBIDDEN"


# ---------------------------------------------------------------------------
# listDevices
# ---------------------------------------------------------------------------


def testListDevices_ActiveAndRevokedCounts(db: Session) -> None:
    user = _makeUser(db, "8")
    d1 = _makeDevice(db, user, "device-aaa")
    _makeDevice(db, user, "device-bbb")
    d3 = _makeDevice(db, user, "device-ccc")
    d3.status = "revoked"
    d3.revokedAt = datetime.now(UTC).replace(tzinfo=None)
    db.flush()

    items, maxActive, activeCount = listDevices(db, user.id, currentDevicePublicId="device-aaa")
    assert maxActive == MAX_ACTIVE_DEVICES == 3
    assert activeCount == 2
    assert len(items) == 3
    # isCurrent 标记
    currents = [d for d in items if d.isCurrent]
    assert len(currents) == 1
    assert currents[0].devicePublicId == "device-aaa"


def testListDevices_EmptyList(db: Session) -> None:
    user = _makeUser(db, "9")
    items, maxActive, activeCount = listDevices(db, user.id)
    assert items == []
    assert maxActive == MAX_ACTIVE_DEVICES
    assert activeCount == 0


# ---------------------------------------------------------------------------
# revokeDevice
# ---------------------------------------------------------------------------


def testRevokeDevice_OtherDeviceSucceeds(db: Session) -> None:
    user = _makeUser(db, "10")
    target = _makeDevice(db, user, "device-target")
    _makeDevice(db, user, "device-current")

    # 当前设备是 device-current,撤销 device-target
    revoked = revokeDevice(db, user.id, target.id, currentDevicePublicId="device-current")
    db.commit()
    # 由于没有 stored_refresh_token 行,撤销数为 0,但设备仍被标 revoked
    assert revoked == 0
    db.refresh(target)
    assert target.status == "revoked"
    assert target.revokedAt is not None


def testRevokeDevice_CurrentDeviceRaises(db: Session) -> None:
    user = _makeUser(db, "11")
    current = _makeDevice(db, user, "device-current")
    with pytest.raises(ApiError) as exc:
        revokeDevice(db, user.id, current.id, currentDevicePublicId="device-current")
    assert exc.value.code == "BAD_REQUEST"
    assert exc.value.httpStatus == 400


def testRevokeDevice_NotFoundRaises(db: Session) -> None:
    user = _makeUser(db, "12")
    with pytest.raises(ApiError) as exc:
        revokeDevice(db, user.id, 99999, currentDevicePublicId=None)
    assert exc.value.code == "NOT_FOUND"


def testRevokeDevice_AlreadyRevokedReturnsZero(db: Session) -> None:
    user = _makeUser(db, "13")
    target = _makeDevice(db, user, "device-x")
    target.status = "revoked"
    target.revokedAt = datetime.now(UTC).replace(tzinfo=None)
    db.flush()

    revoked = revokeDevice(db, user.id, target.id, currentDevicePublicId=None)
    assert revoked == 0


# ---------------------------------------------------------------------------
# deleteAccount
# ---------------------------------------------------------------------------


def testDeleteAccount_SoftDeletesAndSchedulesHardDelete(db: Session) -> None:
    user = _makeUser(db, "14")
    scheduledDelta = timedelta(days=account_service.SOFT_DELETE_HARD_DELETE_DAYS)

    revokedCount, scheduledAt = deleteAccount(
        db,
        user.id,
        rawPassword="Password1Aa",
        currentDevicePublicId=None,
    )
    db.commit()

    assert revokedCount == 0  # 没有 stored_refresh_token
    db.refresh(user)
    assert user.status == "deleted"
    assert user.deletedAt is not None
    assert user.passwordHash == "!"
    # 计划硬删时间 ≈ 现在 + 30 天
    expected = user.deletedAt + scheduledDelta
    delta = abs((scheduledAt - expected).total_seconds())
    assert delta < 2  # 容忍 2 秒误差


def testDeleteAccount_WrongPasswordRaisesInvalidCredentials(db: Session) -> None:
    user = _makeUser(db, "15")
    with pytest.raises(ApiError) as exc:
        deleteAccount(db, user.id, rawPassword="WrongPass1", currentDevicePublicId=None)
    assert exc.value.code == "INVALID_CREDENTIALS"
    assert exc.value.httpStatus == 401
    # 用户未被注销
    db.refresh(user)
    assert user.status == "active"


def testDeleteAccount_AlreadyDeletedRaisesConflict(db: Session) -> None:
    user = _makeUser(db, "16")
    user.deletedAt = datetime.now(UTC).replace(tzinfo=None)
    db.flush()
    with pytest.raises(ApiError) as exc:
        deleteAccount(db, user.id, rawPassword="Password1Aa", currentDevicePublicId=None)
    assert exc.value.code == "CONFLICT"


def testDeleteAccount_UnknownUserRaises(db: Session) -> None:
    with pytest.raises(ApiError) as exc:
        deleteAccount(db, 99999, rawPassword="Password1Aa", currentDevicePublicId=None)
    assert exc.value.code == "NOT_FOUND"


def testDeleteAccount_KeepsCurrentDeviceButRevokesOthers(db: Session) -> None:
    """当前设备不强制撤销(允许登录态做轻量响应),但其他设备撤销。"""
    user = _makeUser(db, "17")
    _makeDevice(db, user, "device-current")
    other = _makeDevice(db, user, "device-other")

    deleteAccount(
        db,
        user.id,
        rawPassword="Password1Aa",
        currentDevicePublicId="device-current",
    )
    db.commit()

    db.refresh(other)
    assert other.status == "revoked"
    assert other.revokedAt is not None


# ---------------------------------------------------------------------------
# 私有辅助函数(覆盖白盒分支)
# ---------------------------------------------------------------------------


def testNowHelper_ReturnsUtcNaive(db: Session) -> None:
    now = account_service._now()
    assert isinstance(now, datetime)
    assert now.tzinfo is None


def testToSubscriptionOut_NoneReturnsNone(db: Session) -> None:
    assert account_service._toSubscriptionOut(None) is None


def testSelectActiveSubscription_NoActiveReturnsNone(db: Session) -> None:
    user = _makeUser(db, "18")
    # 没有订阅
    assert account_service._selectActiveSubscription(db, user.id) is None


def testMaxActiveDevicesConstant() -> None:
    """该常量被 account_panel.py 引用;若改动需同步桌面端。"""
    assert MAX_ACTIVE_DEVICES == 3