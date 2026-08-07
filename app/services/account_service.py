"""P0-A 账号服务:me / patch / devices / delete。

- getMe / patchMe:用户自己的账号信息
- listDevices / revokeDevice:设备管理
- deleteAccount:软删(30 天后硬删)

服务层只做编排,事务边界由调用方(router)用 getDb() 上下文管理。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models.identity import (
    IdentityBalance,
    IdentityDevice,
    User as IdentityUser,
)
from app.models.subscription import Subscription
from app.schemas.account import (
    DeviceOut,
    MeOut,
    MePatchResponse,
    SubscriptionOut,
)
from app.security.password import verifyPassword
from app.services.token_service import revokeAllRefreshTokens

MAX_ACTIVE_DEVICES = 3
# 软删后硬删窗口(P0-A 暂不实现硬删 cron,先做软删 + 30 天延迟)
SOFT_DELETE_HARD_DELETE_DAYS = 30


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _toSubscriptionOut(sub: Subscription | None) -> SubscriptionOut | None:
    if sub is None:
        return None
    return SubscriptionOut(
        subscriptionId=sub.id,
        planCode=sub.planCode,
        status=sub.status,
        startedAt=sub.startedAt,
        currentPeriodStart=sub.currentPeriodStart,
        currentPeriodEnd=sub.currentPeriodEnd,
        expiresAt=sub.expiresAt,
        autoRenew=bool(sub.autoRenew),
        monthlyQuota=int(sub.monthlyQuota or 0),
    )


def _selectActiveSubscription(db: Session, userId: int) -> Subscription | None:
    """返回当前生效订阅(active 状态,expiresAt 还未到)。"""
    now = _now()
    return (
        db.execute(
            select(Subscription)
            .where(
                Subscription.userId == userId,
                Subscription.status == "active",
                Subscription.expiresAt > now,
            )
            .order_by(Subscription.currentPeriodEnd.desc())
            .limit(1)
        ).scalar_one_or_none()
    )


# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------


def getMe(db: Session, userId: int) -> MeOut:
    user = db.get(IdentityUser, userId)
    if user is None or user.deletedAt is not None:
        raise ApiError("NOT_FOUND", "用户不存在", httpStatus=404)
    # IdentityBalance.userId 是 String(36),但服务层以 BIGINT userId 接收。
    # 同时尝试 int / str 两种主键形式,保证正确命中(M7 升级 BIGINT 后可去除)。
    balance = db.get(IdentityBalance, userId) or db.get(IdentityBalance, str(userId))
    balanceAmount = int(balance.balance) if balance is not None else 0
    reserved = int(balance.reserved) if balance is not None else 0
    sub = _selectActiveSubscription(db, userId)
    return MeOut(
        userId=user.id,
        email=user.email,
        displayName=user.displayName,
        tier=user.tier,
        status=user.status,
        balance=balanceAmount,
        reserved=reserved,
        available=balanceAmount - reserved,
        subscription=_toSubscriptionOut(sub),
        emailVerified=bool(user.emailVerified),
        failedLoginCount=int(user.failedLoginCount or 0),
        lockedUntil=user.lockedUntil,
        createdAt=user.createdAt,
    )


def patchMe(db: Session, userId: int, displayName: str) -> MePatchResponse:
    user = db.execute(
        select(IdentityUser).where(IdentityUser.id == userId).with_for_update()
    ).scalar_one_or_none()
    if user is None or user.deletedAt is not None:
        raise ApiError("NOT_FOUND", "用户不存在", httpStatus=404)
    if user.status != "active":
        raise ApiError("FORBIDDEN", "账号状态异常,无法修改", httpStatus=403)
    cleaned = (displayName or "").strip()
    if len(cleaned) > 64:
        raise ApiError(
            "DISPLAY_NAME_INVALID",
            "displayName 长度不能超过 64 字符",
            httpStatus=400,
        )
    user.displayName = cleaned
    db.flush()
    return MePatchResponse(
        userId=user.id,
        displayName=user.displayName,
        updatedAt=user.updatedAt,
    )


# ---------------------------------------------------------------------------
# 设备管理
# ---------------------------------------------------------------------------


def _toDeviceOuts(
    devices: Iterable[IdentityDevice], currentDeviceId: str | None
) -> list[DeviceOut]:
    out: list[DeviceOut] = []
    for d in devices:
        out.append(
            DeviceOut(
                deviceId=d.id,
                devicePublicId=d.deviceId,
                deviceName=d.deviceName,
                platform=d.platform,
                status=d.status,
                firstSeenAt=d.firstSeenAt,
                lastSeenAt=d.lastSeenAt,
                revokedAt=d.revokedAt,
                isCurrent=bool(currentDeviceId) and d.deviceId == currentDeviceId,
            )
        )
    return out


def listDevices(
    db: Session, userId: int, currentDevicePublicId: str | None = None
) -> tuple[list[DeviceOut], int, int]:
    devices = (
        db.execute(
            select(IdentityDevice)
            .where(IdentityDevice.userId == userId)
            .order_by(IdentityDevice.lastSeenAt.desc())
        )
        .scalars()
        .all()
    )
    activeCount = sum(1 for d in devices if d.status == "active")
    return _toDeviceOuts(devices, currentDevicePublicId), MAX_ACTIVE_DEVICES, activeCount


def revokeDevice(
    db: Session, userId: int, deviceRecordId: int, currentDevicePublicId: str | None
) -> int:
    """撤销指定设备的 refresh_token(标 revoked + revoke_jti)。返回撤销条数。

    不允许撤销当前请求所用的设备(防自伤)。
    """
    device = db.execute(
        select(IdentityDevice)
        .where(IdentityDevice.id == deviceRecordId, IdentityDevice.userId == userId)
        .with_for_update()
    ).scalar_one_or_none()
    if device is None:
        raise ApiError("NOT_FOUND", "设备不存在", httpStatus=404)
    if currentDevicePublicId and device.deviceId == currentDevicePublicId:
        raise ApiError("BAD_REQUEST", "不能撤销当前正在使用的设备", httpStatus=400)
    if device.status == "revoked":
        return 0
    return _revokeDeviceInternal(db, device)


def _revokeDeviceInternal(db: Session, device: IdentityDevice) -> int:
    from app.models.stored_refresh_token import StoredRefreshToken
    from app.services.token_revocation_service import revokeJti

    records = (
        db.execute(
            select(StoredRefreshToken)
            .where(
                StoredRefreshToken.deviceId == device.id,
                StoredRefreshToken.revokedAt.is_(None),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    now = _now()
    for record in records:
        record.revokedAt = now
        record.revokeReason = "device_revoked"
        revokeJti(
            db,
            record.jti,
            record.userId,
            "refresh",
            record.expiresAt,
            reason="device_revoked",
        )
    device.status = "revoked"
    device.revokedAt = now
    db.flush()
    return len(records)


# ---------------------------------------------------------------------------
# 注销
# ---------------------------------------------------------------------------


def deleteAccount(
    db: Session,
    userId: int,
    rawPassword: str,
    currentDevicePublicId: str | None,
) -> tuple[int, datetime]:
    """软删账号:status='deleted' + 删除该用户所有 refresh_token。

    返回:
        (revokedCount, scheduledHardDeleteAt)
    """
    user = db.execute(
        select(IdentityUser).where(IdentityUser.id == userId).with_for_update()
    ).scalar_one_or_none()
    if user is None:
        raise ApiError("NOT_FOUND", "用户不存在", httpStatus=404)
    if user.deletedAt is not None or user.status == "deleted":
        raise ApiError("CONFLICT", "账号已注销", httpStatus=409)
    if not verifyPassword(rawPassword, user.passwordHash):
        raise ApiError("INVALID_CREDENTIALS", "密码错误", httpStatus=401)

    now = _now()
    user.status = "deleted"
    user.deletedAt = now
    user.passwordHash = "!"  # 清空密码 hash,即使被读出也无法登录
    revokedCount = revokeAllRefreshTokens(db, userId, "account_deleted")

    # 软删后,把所有 active 设备也标 revoked(防御性)
    devices = (
        db.execute(
            select(IdentityDevice)
            .where(IdentityDevice.userId == userId, IdentityDevice.status == "active")
            .with_for_update()
        )
        .scalars()
        .all()
    )
    for device in devices:
        # 当前设备允许保留(便于其他接口在登录态下做轻量响应),
        # 但其 refresh_token 已在 revokeAllRefreshTokens 中被撤销
        if currentDevicePublicId and device.deviceId == currentDevicePublicId:
            continue
        _revokeDeviceInternal(db, device)

    scheduled = now + timedelta(days=SOFT_DELETE_HARD_DELETE_DAYS)
    db.flush()
    return revokedCount, scheduled


# ---------------------------------------------------------------------------
# 公开别名(供 M5 router 调用)
# ---------------------------------------------------------------------------

get_me = getMe
patch_me = patchMe
list_devices = listDevices
revoke_device = revokeDevice
delete_account = deleteAccount


__all__ = [
    "MAX_ACTIVE_DEVICES",
    "SOFT_DELETE_HARD_DELETE_DAYS",
    "getMe",
    "patchMe",
    "listDevices",
    "revokeDevice",
    "deleteAccount",
    "get_me",
    "patch_me",
    "list_devices",
    "revoke_device",
    "delete_account",
]
