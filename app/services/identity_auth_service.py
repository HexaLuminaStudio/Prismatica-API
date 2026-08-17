"""P0-A 邮箱密码身份服务：register/login/refresh/logout。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import getSettings
from app.errors import ApiError
from app.models.identity import IdentityBalance, IdentityDevice, User
from app.models.stored_refresh_token import StoredRefreshToken
from app.security.jwt import createAccessToken, decodeRefreshToken
from app.security.password import hashPassword, verifyPassword
from app.services.token_revocation_service import revokeJti
from app.services.token_service import hashToken, issueRefreshToken

_settings = getSettings()
MAX_ACTIVE_DEVICES = 3
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15
DUMMY_PASSWORD_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEe.2gHJCzg7.VzX2BGQtxdF6pbM8Zz6p2S"


@dataclass(frozen=True)
class AuthTokens:
    accessToken: str
    refreshToken: str
    expiresIn: int


@dataclass(frozen=True)
class AuthResult:
    user: User
    tokens: AuthTokens


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalizeEmail(email: str) -> str:
    return email.strip().lower()


def registerUser(
    db: Session,
    email: str,
    password: str,
    displayName: str = "",
) -> User:
    normalizedEmail = normalizeEmail(email)
    existing = db.execute(select(User.id).where(User.email == normalizedEmail)).scalar_one_or_none()
    if existing is not None:
        raise ApiError("EMAIL_ALREADY_USED", httpStatus=409)

    try:
        passwordHash = hashPassword(password)
    except ValueError as error:
        raise ApiError("WEAK_PASSWORD", str(error), httpStatus=400) from error
    user = User(
        email=normalizedEmail,
        passwordHash=passwordHash,
        displayName=displayName.strip(),
        tier="free",
        status="active",
    )
    db.add(user)
    try:
        db.flush()
    except IntegrityError as error:
        db.rollback()
        raise ApiError("EMAIL_ALREADY_USED", httpStatus=409) from error
    db.add(IdentityBalance(userId=user.id))
    db.flush()
    return user


def _raiseInvalidLogin(db: Session, user: User | None) -> None:
    if user is None:
        raise ApiError("INVALID_CREDENTIALS", httpStatus=401)
    user.failedLoginCount = int(user.failedLoginCount or 0) + 1
    if user.failedLoginCount >= MAX_FAILED_LOGINS:
        user.failedLoginCount = MAX_FAILED_LOGINS
        user.lockedUntil = _now() + timedelta(minutes=LOCK_MINUTES)
        db.commit()
        raise ApiError(
            "ACCOUNT_LOCKED",
            httpStatus=423,
            details={"lockedMinutes": LOCK_MINUTES},
        )
    db.commit()
    raise ApiError("INVALID_CREDENTIALS", httpStatus=401)


def _upsertDevice(
    db: Session,
    userId: int,
    deviceId: str,
    deviceName: str,
    platform: str,
) -> IdentityDevice:
    device = db.execute(
        select(IdentityDevice)
        .where(IdentityDevice.userId == userId, IdentityDevice.deviceId == deviceId)
        .with_for_update()
    ).scalar_one_or_none()
    needsActivation = device is None or device.status != "active"
    if needsActivation:
        activeCount = db.execute(
            select(func.count())
            .select_from(IdentityDevice)
            .where(IdentityDevice.userId == userId, IdentityDevice.status == "active")
        ).scalar_one()
        if int(activeCount or 0) >= MAX_ACTIVE_DEVICES:
            # 保留 MAX_DEVICES_REACHED 作为 envelope code(桌面端 cloud_api 拦截),
            # 内部语义归类到 TOO_MANY_DEVICES(由 errors.py 同时声明,便于后续统一)。
            raise ApiError(
                "MAX_DEVICES_REACHED",
                httpStatus=403,
                details={"limit": MAX_ACTIVE_DEVICES, "category": "TOO_MANY_DEVICES"},
            )

    now = _now()
    if device is None:
        device = IdentityDevice(
            userId=userId,
            deviceId=deviceId,
            deviceName=deviceName,
            platform=platform,
            status="active",
            firstSeenAt=now,
            lastSeenAt=now,
        )
        db.add(device)
        db.flush()
    else:
        device.deviceName = deviceName
        device.platform = platform
        device.status = "active"
        device.revokedAt = None
        device.lastSeenAt = now
    return device


def loginUser(
    db: Session,
    email: str,
    password: str,
    deviceId: str,
    deviceName: str = "",
    platform: str = "",
    clientIp: str | None = None,
) -> AuthResult:
    del clientIp  # M9 audit 接入时使用
    normalizedEmail = normalizeEmail(email)
    user = db.execute(select(User).where(User.email == normalizedEmail).with_for_update()).scalar_one_or_none()
    if user is None:
        verifyPassword(password, DUMMY_PASSWORD_HASH)
        _raiseInvalidLogin(db, None)

    assert user is not None
    now = _now()
    if user.status != "active" or user.deletedAt is not None:
        raise ApiError("INVALID_CREDENTIALS", httpStatus=401)
    if user.lockedUntil is not None and user.lockedUntil > now:
        retryAfter = max(1, int((user.lockedUntil - now).total_seconds()))
        raise ApiError(
            "ACCOUNT_LOCKED",
            httpStatus=423,
            details={"retryAfter": retryAfter},
        )
    if not verifyPassword(password, user.passwordHash):
        _raiseInvalidLogin(db, user)

    user.failedLoginCount = 0
    user.lockedUntil = None
    device = _upsertDevice(db, user.id, deviceId, deviceName, platform)
    accessToken = createAccessToken(
        user.id,
        deviceId,
        user.tier,
        authVersion=int(user.authVersion or 0),
    )
    refreshToken, _record = issueRefreshToken(
        db,
        user.id,
        device.id,
        deviceId,
        authVersion=int(user.authVersion or 0),
    )
    db.flush()
    return AuthResult(
        user=user,
        tokens=AuthTokens(
            accessToken=accessToken,
            refreshToken=refreshToken,
            expiresIn=_settings.jwtAccessTtlSec,
        ),
    )


def refreshUserTokens(db: Session, rawRefreshToken: str, requestDeviceId: str) -> AuthResult:
    try:
        claims = decodeRefreshToken(rawRefreshToken)
    except pyjwt.ExpiredSignatureError as error:
        raise ApiError("REFRESH_EXPIRED", httpStatus=401) from error
    except pyjwt.InvalidTokenError as error:
        raise ApiError("REFRESH_INVALID", httpStatus=401) from error
    if not requestDeviceId or claims.get("device_id") != requestDeviceId:
        raise ApiError("REFRESH_INVALID", "设备与刷新凭证不匹配", httpStatus=401)

    record = db.execute(
        select(StoredRefreshToken).where(StoredRefreshToken.tokenHash == hashToken(rawRefreshToken)).with_for_update()
    ).scalar_one_or_none()
    if record is None or record.jti != claims.get("jti"):
        raise ApiError("REFRESH_INVALID", httpStatus=401)
    if record.revokedAt is not None:
        raise ApiError("TOKEN_REVOKED", httpStatus=401)
    if record.expiresAt <= _now():
        raise ApiError("REFRESH_EXPIRED", httpStatus=401)

    user = db.get(User, int(record.userId) if record.userId is not None else 0)
    device = db.get(IdentityDevice, int(record.deviceId) if record.deviceId is not None else 0)
    if (
        user is None
        or user.status != "active"
        or user.deletedAt is not None
        or device is None
        or str(device.userId) != str(user.id)
        or device.deviceId != requestDeviceId
        or device.status != "active"
    ):
        raise ApiError("REFRESH_INVALID", httpStatus=401)
    if int(claims.get("auth_version", 0)) != int(user.authVersion or 0):
        raise ApiError("TOKEN_REVOKED", httpStatus=401)

    now = _now()
    record.revokedAt = now
    record.revokeReason = "rotated"
    refreshToken, newRecord = issueRefreshToken(
        db,
        user.id,
        device.id,
        device.deviceId,
        authVersion=int(user.authVersion or 0),
    )
    record.replacedByJti = newRecord.jti
    revokeJti(
        db,
        record.jti,
        user.id,
        "refresh",
        record.expiresAt,
        reason="rotated",
    )
    device.lastSeenAt = now
    accessToken = createAccessToken(
        user.id,
        device.deviceId,
        user.tier,
        authVersion=int(user.authVersion or 0),
    )
    db.flush()
    return AuthResult(
        user=user,
        tokens=AuthTokens(
            accessToken=accessToken,
            refreshToken=refreshToken,
            expiresIn=_settings.jwtAccessTtlSec,
        ),
    )


def logoutUser(db: Session, rawRefreshToken: str | None) -> bool:
    if not rawRefreshToken:
        return False
    try:
        decodeRefreshToken(rawRefreshToken)
    except pyjwt.InvalidTokenError:
        return False
    record = db.execute(
        select(StoredRefreshToken).where(StoredRefreshToken.tokenHash == hashToken(rawRefreshToken)).with_for_update()
    ).scalar_one_or_none()
    if record is None or record.revokedAt is not None:
        return False
    record.revokedAt = _now()
    record.revokeReason = "logout"
    revokeJti(
        db,
        record.jti,
        record.userId,
        "refresh",
        record.expiresAt,
        reason="logout",
    )
    db.flush()
    return True


register_user = registerUser
login_user = loginUser
refresh_tokens = refreshUserTokens
logout = logoutUser

__all__ = [
    "AuthResult",
    "AuthTokens",
    "registerUser",
    "loginUser",
    "refreshUserTokens",
    "logoutUser",
    "register_user",
    "login_user",
    "refresh_tokens",
    "logout",
]
