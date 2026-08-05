"""认证服务:redeem / refresh / logout。

按 PRD v2 §3 实现:
    - redeem:验签 + 幂等 + 创建 user / balance / device / refresh_token,下发 JWT
    - refresh:校验 refresh_token,重发 access + 滚动 refresh(返回相同结构)
    - logout:revoke 当前 refresh_token(可选 revoke device 所有 token)
"""
from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import getSettings
from app.errors import ApiError
from app.models import (
    LicenseCodeSeen,
    RechargeRecord,
    RefreshToken,
    UserAccount,
    UserBalance,
    UserDevice,
)
from app.models.license_models import InviteCode, RechargeCode, TrialCode
from app.schemas.auth import (
    BalanceOut,
    RedeemResponse,
    TokensOut,
    UserOut,
)
from app.security import hmac as hmacUtil
from app.security.jwt import encodeAccessToken

_settings = getSettings()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _newUserId() -> str:
    return str(uuid.uuid4())


def _genRefreshTokenId() -> str:
    # 表 token_id CHAR(36),用 token_hex(16) → 32 hex 字符(预留 4 字符)
    return secrets.token_hex(16)


def _ensureDevice(
    db: Session, userId: str, deviceId: str, deviceName: str, platform: str
) -> None:
    """upsert device(若不存在则创建)。"""
    device = db.get(UserDevice, deviceId)
    if device is None:
        device = UserDevice(
            deviceId=deviceId,
            userId=userId,
            deviceName=deviceName or "",
            platform=platform or "",
        )
        db.add(device)
    else:
        device.userId = userId
        if deviceName:
            device.deviceName = deviceName
        if platform:
            device.platform = platform
    db.flush()


def _ensureBalance(db: Session, userId: str) -> UserBalance:
    """确保 user_balances 行存在(余额为 0)。"""
    balance = db.get(UserBalance, userId)
    if balance is None:
        balance = UserBalance(userId=userId)
        db.add(balance)
        db.flush()
    return balance


def _writeRechargeRecord(
    db: Session,
    userId: str,
    amount: int,
    source: str,
    balanceBefore: int,
    balanceAfter: int,
    codeHash: str | None = None,
    operatorNote: str = "",
) -> RechargeRecord:
    record = RechargeRecord(
        recordId=str(uuid.uuid4()),
        userId=userId,
        amount=amount,
        source=source,
        codeHash=codeHash,
        operatorNote=operatorNote,
        balanceBefore=balanceBefore,
        balanceAfter=balanceAfter,
    )
    db.add(record)
    db.flush()
    return record


def _buildRedeemResponse(
    db: Session,
    user: UserAccount,
    balance: UserBalance,
    deviceId: str,
    tier: str,
    refreshTokenId: str,
    mode: str,
) -> RedeemResponse:
    accessToken, ttl = encodeAccessToken(user.userId, deviceId, tier=tier)
    return RedeemResponse(
        mode=mode,
        user=UserOut(
            userId=user.userId,
            displayName=user.displayName,
            tier=user.tier,
            createdAt=user.createdAt,
        ),
        balance=BalanceOut(
            balance=balance.balance,
            frozenBalance=balance.frozenBalance,
            totalSpent=balance.totalSpent,
            totalRecharged=balance.totalRecharged,
        ),
        tokens=TokensOut(
            accessToken=accessToken,
            refreshToken=refreshTokenId,
            expiresIn=ttl,
        ),
    )


def _issueRefreshToken(
    db: Session,
    userId: str,
    deviceId: str,
) -> RefreshToken:
    """签发 refresh_token(opaque UUID),存表。"""
    token = RefreshToken(
        tokenId=_genRefreshTokenId(),
        userId=userId,
        deviceId=deviceId,
        expiresAt=datetime.now(UTC)
        + timedelta(seconds=_settings.jwtRefreshTtlSec),
    )
    db.add(token)
    db.flush()
    return token


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def _parseAndVerify(rawCode: str):
    """解码 + 验签 + 反序列化为 Pydantic 模型。

    Returns:
        (kind: str, model, signature)
    """
    try:
        data = hmacUtil.decodeSignedCode(rawCode)
    except ValueError as e:
        raise ApiError("INVALID_CODE", f"码无效或已损坏: {e}") from e
    signature = data.get("signature")
    if not signature:
        raise ApiError("INVALID_CODE", "凭证缺少 signature 字段")
    payloadWithoutSig = {k: v for k, v in data.items() if k != "signature"}
    if not hmacUtil.verifyPayload(payloadWithoutSig, signature):
        raise ApiError("INVALID_CODE", "凭证签名校验失败")

    codeField = data.get("code", "")
    if not codeField:
        raise ApiError("INVALID_CODE", "凭证缺少 code 字段")
    prefix = codeField.split("-", 1)[0]

    try:
        if prefix == "INV":
            return "invite", InviteCode.model_validate(payloadWithoutSig)
        if prefix == "TRY":
            return "trial", TrialCode.model_validate(payloadWithoutSig)
        if prefix == "RCH":
            return "recharge", RechargeCode.model_validate(payloadWithoutSig)
    except Exception as e:
        raise ApiError("INVALID_CODE", f"凭证字段不合法: {e}") from e

    raise ApiError("INVALID_CODE", f"未知凭证类型: {prefix}")


def redeemCode(
    db: Session,
    rawCode: str,
    deviceId: str,
    deviceName: str = "",
    platform: str = "",
    displayName: str = "内测用户",
    clientIp: str | None = None,
) -> RedeemResponse:
    """统一兑换入口(自动识别 INV/TRY/RCH)。"""
    kind, model = _parseAndVerify(rawCode)
    codeHash = hmacUtil.hashCode(rawCode)

    # 1) 全局幂等
    existing = db.get(LicenseCodeSeen, codeHash)
    if existing is not None and existing.consumedAt is not None:
        raise ApiError("ALREADY_USED", "该凭证已被使用")

    # 2) 过期判断
    now = datetime.now(UTC).replace(tzinfo=None)  # 与 MySQL TIMESTAMP 比较保持 naive
    expireAt: datetime = model.expireAt
    if expireAt.tzinfo is not None:
        expireAt = expireAt.astimezone(UTC).replace(tzinfo=None)
    if expireAt < now:
        raise ApiError("EXPIRED", "该凭证已过期")

    # 3) 邀请/体验:需创建 user + balance;充值:需要先有 user
    if kind in ("invite", "trial"):
        # 邀请/体验:用 code.code 作为 userId(同一码→同一 user,支持跨设备)
        userId = codeHash[:36]  # 取前 36 字符作为 UUID-like
        user = db.get(UserAccount, userId)
        if user is None:
            user = UserAccount(
                userId=userId,
                displayName=displayName or model.code,
                tier=model.tier.value if hasattr(model.tier, "value") else str(model.tier),
                status="active",
                activatedAt=now,
                expireAt=now + timedelta(days=model.grantedDays),
            )
            db.add(user)
            db.flush()
        elif user.status == "active":
            # 已激活且凭证未过期:再次输入相同 INV/TRY → 重发 token(支持跨设备)
            pass
        else:
            raise ApiError("ALREADY_AUTHENTICATED", "已存在激活凭证,请先注销后再兑换")

        balance = _ensureBalance(db, userId)
        beforeBalance = balance.balance
        balance.balance += model.grantedBalance
        balance.totalRecharged += model.grantedBalance
        balance.version += 1
        db.flush()
        _writeRechargeRecord(
            db,
            userId=userId,
            amount=model.grantedBalance,
            source=f"{kind}_grant",
            balanceBefore=beforeBalance,
            balanceAfter=balance.balance,
            codeHash=codeHash,
            operatorNote=f"{kind} {model.code}",
        )

        # 写幂等表(若 INSERT 冲突说明并发已消费 → 409)
        try:
            db.add(
                LicenseCodeSeen(
                    codeHash=codeHash,
                    codeKind=kind,
                    issuedAt=model.issuedAt,
                    consumedAt=now,
                    consumedByUserId=userId,
                    consumeIp=clientIp,
                    expireAt=expireAt,
                )
            )
            db.flush()
        except IntegrityError:
            db.rollback()
            raise ApiError("ALREADY_USED", "该凭证已被使用") from None

        _ensureDevice(db, userId, deviceId, deviceName, platform)
        refresh = _issueRefreshToken(db, userId, deviceId)
        db.commit()

        logger.info(
            f"[Auth] {kind} 激活成功 user={userId} device={deviceId[:8]}... "
            f"balance={balance.balance}"
        )
        return _buildRedeemResponse(
            db, user, balance, deviceId,
            tier=model.tier.value if hasattr(model.tier, "value") else str(model.tier),
            refreshTokenId=refresh.tokenId,
            mode=kind,
        )

    # 充值码:必须先有 user
    if not model.amount or model.amount <= 0:
        raise ApiError("INVALID_CODE", "充值码金额无效")

    # 充值码幂等:已用 → 409
    if existing is not None and existing.rechargeUserId is not None:
        raise ApiError("ALREADY_USED", "该充值码已被使用")

    # 充值需要 user;此处复用 deviceId 反查 user
    device = db.get(UserDevice, deviceId)
    if device is None:
        raise ApiError("NEED_ACTIVATION", "请先激活后再使用充值码")
    userId = device.userId
    user = db.get(UserAccount, userId)
    if user is None:
        raise ApiError("NEED_ACTIVATION", "请先激活后再使用充值码")

    balance = _ensureBalance(db, userId)
    beforeBalance = balance.balance
    balance.balance += model.amount
    balance.totalRecharged += model.amount
    balance.version += 1
    db.flush()
    _writeRechargeRecord(
        db,
        userId=userId,
        amount=model.amount,
        source="recharge_code",
        codeHash=codeHash,
        balanceBefore=beforeBalance,
        balanceAfter=balance.balance,
        operatorNote=model.note or f"recharge {model.code}",
    )

    try:
        if existing is None:
            db.add(
                LicenseCodeSeen(
                    codeHash=codeHash,
                    codeKind="recharge",
                    issuedAt=model.issuedAt,
                    consumedAt=now,
                    consumedByUserId=userId,
                    consumeIp=clientIp,
                    rechargeUserId=userId,
                    rechargeAmount=model.amount,
                    expireAt=expireAt,
                )
            )
        else:
            existing.consumedAt = now
            existing.consumedByUserId = userId
            existing.consumeIp = clientIp
            existing.rechargeUserId = userId
            existing.rechargeAmount = model.amount
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError("ALREADY_USED", "该充值码已被使用") from None

    refresh = _issueRefreshToken(db, userId, deviceId)
    db.commit()

    logger.info(
        f"[Auth] recharge 成功 user={userId} device={deviceId[:8]}... "
        f"+{model.amount} balance={balance.balance}"
    )
    return _buildRedeemResponse(
        db, user, balance, deviceId, tier=user.tier, refreshTokenId=refresh.tokenId, mode="recharge"
    )


def refreshTokens(db: Session, refreshToken: str, deviceId: str) -> RedeemResponse:
    """用 refresh_token 换新的 access + 滚动 refresh。"""
    token = db.get(RefreshToken, refreshToken)
    if token is None:
        raise ApiError("REFRESH_INVALID", httpStatus=401)
    if token.revokedAt is not None:
        raise ApiError("REFRESH_INVALID", httpStatus=401)
    expiresAt = token.expiresAt
    if expiresAt.tzinfo is not None:
        expiresAt = expiresAt.astimezone(UTC).replace(tzinfo=None)
    if expiresAt < datetime.now(UTC).replace(tzinfo=None):
        raise ApiError("REFRESH_EXPIRED", httpStatus=401)

    user = db.get(UserAccount, token.userId)
    if user is None:
        raise ApiError("REFRESH_INVALID", httpStatus=401)
    balance = _ensureBalance(db, user.userId)

    # 滚动续期:revoke 老 refresh + 发新 refresh(保证旧 token 一旦泄露立即失效)
    token.revokedAt = datetime.now(UTC).replace(tzinfo=None)
    newRefresh = _issueRefreshToken(db, user.userId, deviceId or token.deviceId)
    db.commit()

    return _buildRedeemResponse(
        db,
        user,
        balance,
        deviceId or token.deviceId,
        tier=user.tier,
        refreshTokenId=newRefresh.tokenId,
        mode="refresh",
    )


def revokeRefreshToken(db: Session, refreshToken: str | None) -> None:
    """登出:撤销指定的 refresh_token;若 None 则撤销整个 device 的 token。"""
    now = datetime.now(UTC).replace(tzinfo=None)
    if refreshToken:
        token = db.get(RefreshToken, refreshToken)
        if token is not None and token.revokedAt is None:
            token.revokedAt = now
    db.commit()
