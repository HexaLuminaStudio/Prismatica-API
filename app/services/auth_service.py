"""兑换码认证服务。

兑换入口与邮箱密码登录共用 P0-A 的 BIGINT 用户、设备、余额和刷新令牌模型。
支持签名载荷以及后台签发后展示给用户的明文 INV/TRY/RCH 码。
"""

from __future__ import annotations

import math
import secrets
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import getSettings
from app.errors import ApiError
from app.models import CodeRedemption, LicenseCode, Subscription
from app.models.identity import IdentityBalance, IdentityDevice, User
from app.models.user import ActivationCode, InviteCode, RechargeCode, TrialCode
from app.schemas.auth import BalanceOut, RedeemResponse, TokensOut, UserOut
from app.security import hmac as hmacUtil
from app.security.jwt import createAccessToken
from app.security.password import hashPassword
from app.services.identity_auth_service import logoutUser, refreshUserTokens
from app.services.subscription_service import (
    redeemInviteCode,
    redeemRechargeCode,
    redeemTrialCode,
)
from app.services.token_service import issueRefreshToken

_settings = getSettings()

_DISPLAY_KIND = {"INV": "invite", "TRY": "trial", "RCH": "recharge"}
_STORAGE_KIND = {value: key for key, value in _DISPLAY_KIND.items()}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _toNaive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _canonicalTier(value: str | None) -> str:
    """把存量兑换码档位收敛到 users 表允许的 free/pro/team。"""
    normalized = (value or "").strip().lower()
    if normalized == "team":
        return "team"
    if normalized in {"pro", "paid", "beta", "beta_pro", "trial", "正式用户", "正式版", "普通用户", "内测用户"}:
        return "pro"
    return "free"


def _findLicense(db: Session, codeHash: str, *, lock: bool = False) -> LicenseCode | None:
    stmt = select(LicenseCode).where(LicenseCode.codeHash == codeHash)
    if lock:
        stmt = stmt.with_for_update()
    return db.execute(stmt).scalar_one_or_none()


def _decodeRawCode(rawCode: str, db: Session) -> dict:
    """解码签名载荷，或从 license_codes 反查明文码元数据。"""
    try:
        return hmacUtil.decodeSignedCode(rawCode)
    except (TypeError, ValueError):
        pass

    codeBody = (rawCode or "").strip()
    if not any(codeBody.startswith(prefix) for prefix in ("INV-", "TRY-", "RCH-")):
        raise ApiError("INVALID_CODE", "码无效或已损坏: 凭证格式错误")

    row = _findLicense(db, hmacUtil.hashCode(codeBody))
    if row is None:
        raise ApiError("INVALID_CODE", "码无效或已损坏: 未找到凭证或凭证已下线")
    if row.expiresAt is None:
        raise ApiError("INVALID_CODE", "码无效或已损坏: 凭证缺失有效期")

    payload: dict = {
        "code": codeBody,
        "issuedAt": (row.issuedAt or _now()).isoformat(),
        "expireAt": row.expiresAt.isoformat(),
        "version": 1,
    }
    if row.codeKind == "INV":
        payload.update(
            {
                "maxUses": row.maxUses,
                "grantedBalance": int(row.monthlyQuota or 0),
                "grantedDays": int(row.periodMonths or 0) * 30,
                "tier": row.planCode or "pro",
            }
        )
    elif row.codeKind == "TRY":
        payload.update(
            {
                "maxUses": row.maxUses,
                "grantedBalance": int(row.monthlyQuota or 0),
                "grantedDays": int(row.trialDays or 0),
                "tier": "pro",
            }
        )
    elif row.codeKind == "RCH":
        payload.update({"amount": int(row.amount or 0), "note": row.note})
    else:
        raise ApiError("INVALID_CODE", "码无效或已损坏: 未知凭证类型")
    return payload


def _parseAndVerify(rawCode: str, db: Session):
    data = _decodeRawCode(rawCode, db)
    signature = data.get("signature")
    unsigned = {key: value for key, value in data.items() if key != "signature"}
    if signature:
        if not hmacUtil.verifyPayload(unsigned, signature):
            raise ApiError("INVALID_CODE", "凭证签名校验失败")
    elif not any((rawCode or "").strip().startswith(prefix) for prefix in ("INV-", "TRY-", "RCH-")):
        raise ApiError("INVALID_CODE", "凭证格式错误")

    codeBody = str(data.get("code") or "")
    prefix = codeBody.split("-", 1)[0] if codeBody else ""
    try:
        if prefix == "INV":
            return "invite", InviteCode.model_validate(unsigned)
        if prefix == "TRY":
            return "trial", TrialCode.model_validate(unsigned)
        if prefix == "RCH":
            return "recharge", RechargeCode.model_validate(unsigned)
        if not codeBody and (data.get("validityPeriod") or data.get("deviceCode")):
            return "activation", ActivationCode.model_validate(unsigned)
    except Exception as error:
        raise ApiError("INVALID_CODE", f"凭证字段不合法: {error}") from error

    if not codeBody:
        raise ApiError("INVALID_CODE", "凭证缺少 code 字段")
    raise ApiError("INVALID_CODE", f"未知凭证类型: {prefix}")


def _codeExpiry(kind: str, model) -> datetime:
    if kind != "activation":
        return _toNaive(model.expireAt)
    validity = getattr(model, "validityPeriod", None)
    if not validity:
        raise ApiError("INVALID_CODE", "激活码缺少有效期")
    try:
        return datetime.strptime(validity, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError as error:
        raise ApiError("INVALID_CODE", "激活码有效期字段不合法") from error


def _codeHash(rawCode: str, model) -> str:
    codeBody = getattr(model, "code", None)
    return hmacUtil.hashCode(codeBody or rawCode)


def _findPublicDevice(db: Session, deviceId: str) -> IdentityDevice | None:
    return db.execute(
        select(IdentityDevice)
        .where(IdentityDevice.deviceId == deviceId, IdentityDevice.status == "active")
        .order_by(IdentityDevice.id)
        .limit(1)
    ).scalar_one_or_none()


def _upsertDevice(
    db: Session,
    userId: int,
    deviceId: str,
    deviceName: str,
    platform: str,
) -> IdentityDevice:
    device = db.execute(
        select(IdentityDevice).where(
            IdentityDevice.userId == userId,
            IdentityDevice.deviceId == deviceId,
        )
    ).scalar_one_or_none()
    now = _now()
    if device is None:
        device = IdentityDevice(
            userId=userId,
            deviceId=deviceId,
            deviceName=deviceName or "",
            platform=platform or "",
            status="active",
            firstSeenAt=now,
            lastSeenAt=now,
        )
        db.add(device)
        db.flush()
    else:
        device.deviceName = deviceName or device.deviceName
        device.platform = platform or device.platform
        device.status = "active"
        device.revokedAt = None
        device.lastSeenAt = now
    return device


def _ensureBalance(db: Session, userId: int) -> IdentityBalance:
    balance = db.get(IdentityBalance, userId)
    if balance is None:
        balance = IdentityBalance(userId=userId)
        db.add(balance)
        db.flush()
    return balance


def _createRedeemUser(db: Session, displayName: str, tier: str) -> User:
    user = User(
        email=f"redeem-{secrets.token_hex(16)}@local.invalid",
        passwordHash=hashPassword(f"Redeem-2026-{secrets.token_urlsafe(32)}"),
        displayName=(displayName or "内测用户").strip(),
        tier=tier,
        status="active",
    )
    db.add(user)
    db.flush()
    db.add(IdentityBalance(userId=user.id))
    db.flush()
    return user


def _createLicense(
    db: Session,
    codeHash: str,
    kind: str,
    model,
    expiresAt: datetime,
) -> LicenseCode:
    storageKind = "INV" if kind == "activation" else _STORAGE_KIND[kind]
    grantedDays = (
        max(0, int((expiresAt - _now()).total_seconds() // 86400))
        if kind == "activation"
        else int(getattr(model, "grantedDays", 0) or 0)
    )
    grantedBalance = int(getattr(model, "grantedBalance", 0) or 0)
    tier = _canonicalTier(getattr(model, "userType", None) if kind == "activation" else str(getattr(model, "tier", "")))
    row = LicenseCode(
        codeHash=codeHash,
        codeKind=storageKind,
        status="active",
        planCode=tier if storageKind == "INV" else None,
        periodMonths=max(1, math.ceil(grantedDays / 30)) if storageKind == "INV" and grantedDays else None,
        trialDays=grantedDays if storageKind == "TRY" else None,
        monthlyQuota=grantedBalance if storageKind in {"INV", "TRY"} else None,
        amount=int(getattr(model, "amount", 0) or 0) if storageKind == "RCH" else None,
        maxUses=int(getattr(model, "maxUses", 1) or 1),
        usedCount=0,
        note="redeem import",
        issuedAt=_toNaive(getattr(model, "issuedAt", _now())),
        expiresAt=expiresAt,
    )
    db.add(row)
    db.flush()
    return row


def _firstRedemption(db: Session, codeId: int) -> CodeRedemption | None:
    return db.execute(
        select(CodeRedemption).where(CodeRedemption.codeId == codeId).order_by(CodeRedemption.id).limit(1)
    ).scalar_one_or_none()


def _userRedemption(db: Session, codeId: int, userId: int) -> CodeRedemption | None:
    return db.execute(
        select(CodeRedemption).where(
            CodeRedemption.codeId == codeId,
            CodeRedemption.userId == userId,
        )
    ).scalar_one_or_none()


def _activeExpiry(db: Session, userId: int) -> datetime | None:
    return db.execute(
        select(Subscription.expiresAt)
        .where(Subscription.userId == userId, Subscription.status == "active")
        .order_by(Subscription.expiresAt.desc())
        .limit(1)
    ).scalar_one_or_none()


def _buildResponse(
    db: Session,
    *,
    mode: str,
    user: User,
    device: IdentityDevice,
) -> RedeemResponse:
    balance = _ensureBalance(db, user.id)
    authVersion = int(user.authVersion or 0)
    accessToken = createAccessToken(
        user.id,
        device.deviceId,
        user.tier,
        authVersion=authVersion,
    )
    refreshToken, _record = issueRefreshToken(
        db,
        user.id,
        device.id,
        device.deviceId,
        authVersion=authVersion,
    )
    db.flush()
    return RedeemResponse(
        mode=mode,
        user=UserOut(
            userId=str(user.id),
            displayName=user.displayName,
            tier=user.tier,
            createdAt=user.createdAt,
            expireAt=_activeExpiry(db, user.id),
        ),
        balance=BalanceOut(
            balance=int(balance.balance or 0),
            frozenBalance=int(balance.reserved or 0),
            totalSpent=int(balance.lifetimeConsumed or 0),
            totalRecharged=int(balance.lifetimeGrant or 0),
        ),
        tokens=TokensOut(
            accessToken=accessToken,
            refreshToken=refreshToken,
            expiresIn=_settings.jwtAccessTtlSec,
        ),
    )


def redeemCode(
    db: Session,
    rawCode: str,
    deviceId: str,
    deviceName: str = "",
    platform: str = "",
    displayName: str = "内测用户",
    clientIp: str | None = None,
) -> RedeemResponse:
    """兑换 INV/TRY/RCH/存量激活码，并签发与登录接口一致的 JWT。"""
    if not deviceId.strip():
        raise ApiError("BAD_REQUEST", "deviceId 不能为空")

    kind, model = _parseAndVerify(rawCode, db)
    expiresAt = _codeExpiry(kind, model)
    now = _now()
    if expiresAt < now:
        raise ApiError("EXPIRED", "该凭证已过期")

    codeHash = _codeHash(rawCode, model)
    licenseRow = _findLicense(db, codeHash, lock=True)
    if licenseRow is not None:
        if licenseRow.status == "revoked":
            raise ApiError("INVALID_CODE", "该凭证已被撤销")
        if licenseRow.status == "expired":
            raise ApiError("EXPIRED", "该凭证已过期")
        if licenseRow.expiresAt is not None and licenseRow.expiresAt < now:
            licenseRow.status = "expired"
            raise ApiError("EXPIRED", "该凭证已过期")

    publicDevice = _findPublicDevice(db, deviceId)
    firstRedemption = _firstRedemption(db, licenseRow.id) if licenseRow is not None else None
    exhausted = bool(
        licenseRow is not None and (licenseRow.status == "exhausted" or licenseRow.usedCount >= licenseRow.maxUses)
    )

    if kind == "recharge":
        if exhausted:
            raise ApiError("ALREADY_USED", "该充值码已被使用")
        if publicDevice is None:
            raise ApiError("NEED_ACTIVATION", "请先激活后再使用充值码")
        user = db.get(User, publicDevice.userId)
    elif firstRedemption is not None and exhausted:
        if publicDevice is not None and publicDevice.userId != firstRedemption.userId:
            raise ApiError("ALREADY_USED", "该凭证已绑定其他账号")
        user = db.get(User, firstRedemption.userId)
    elif publicDevice is not None:
        user = db.get(User, publicDevice.userId)
    else:
        requestedTier = (
            getattr(model, "userType", None) if kind == "activation" else str(getattr(model, "tier", "free"))
        )
        user = _createRedeemUser(db, displayName, _canonicalTier(requestedTier))

    if user is None:
        raise ApiError("INVALID_CODE", "凭证关联的用户不存在")
    if user.status != "active" or user.deletedAt is not None:
        raise ApiError("ALREADY_AUTHENTICATED", "账号状态异常,无法兑换")

    device = _upsertDevice(db, user.id, deviceId, deviceName, platform)
    if licenseRow is None:
        licenseRow = _createLicense(db, codeHash, kind, model, expiresAt)

    alreadyRedeemed = _userRedemption(db, licenseRow.id, user.id)
    shouldGrant = not exhausted and alreadyRedeemed is None
    if shouldGrant:
        grantedBalance = int(getattr(model, "grantedBalance", 0) or 0)
        subscription = None
        if kind == "recharge":
            grantedBalance = int(model.amount)
            redeemRechargeCode(db, user.id, grantedBalance, licenseRow.id)
        elif kind == "trial":
            subscription = redeemTrialCode(
                db,
                user.id,
                grantedBalance,
                int(model.grantedDays or 0),
                licenseRow.id,
            )
            user.tier = "pro"
        else:
            grantedDays = (
                max(1, int((expiresAt - now).total_seconds() // 86400))
                if kind == "activation"
                else int(model.grantedDays or 0)
            )
            subscription, _amount = redeemInviteCode(
                db,
                user.id,
                grantedBalance,
                grantedDays,
                licenseRow.id,
                clientIp,
            )
            if kind == "activation" and subscription is not None:
                subscription.currentPeriodEnd = expiresAt
                subscription.expiresAt = expiresAt
                subscription.nextGrantAt = expiresAt
            user.tier = _canonicalTier(
                getattr(model, "userType", None) if kind == "activation" else str(getattr(model, "tier", "pro"))
            )

        db.add(
            CodeRedemption(
                codeId=licenseRow.id,
                userId=user.id,
                subscriptionId=subscription.id if subscription is not None else None,
                amountGranted=grantedBalance,
                clientIp=clientIp,
            )
        )
        licenseRow.usedCount = int(licenseRow.usedCount or 0) + 1
        if licenseRow.usedCount >= licenseRow.maxUses:
            licenseRow.status = "exhausted"
        db.flush()

    response = _buildResponse(db, mode=kind, user=user, device=device)
    db.commit()
    logger.info(f"[Auth] {kind} redeem user={user.id} device={deviceId[:8]}... granted={shouldGrant}")
    return response


def refreshTokens(db: Session, refreshToken: str, deviceId: str) -> RedeemResponse:
    """兼容旧服务调用方，内部使用当前 JWT refresh 轮换实现。"""
    result = refreshUserTokens(db, refreshToken, deviceId)
    balance = _ensureBalance(db, result.user.id)
    return RedeemResponse(
        mode="refresh",
        user=UserOut(
            userId=str(result.user.id),
            displayName=result.user.displayName,
            tier=result.user.tier,
            createdAt=result.user.createdAt,
            expireAt=_activeExpiry(db, result.user.id),
        ),
        balance=BalanceOut(
            balance=int(balance.balance or 0),
            frozenBalance=int(balance.reserved or 0),
            totalSpent=int(balance.lifetimeConsumed or 0),
            totalRecharged=int(balance.lifetimeGrant or 0),
        ),
        tokens=TokensOut(
            accessToken=result.tokens.accessToken,
            refreshToken=result.tokens.refreshToken,
            expiresIn=result.tokens.expiresIn,
        ),
    )


def revokeRefreshToken(db: Session, refreshToken: str | None) -> None:
    logoutUser(db, refreshToken)
    db.commit()


__all__ = ["redeemCode", "refreshTokens", "revokeRefreshToken"]
