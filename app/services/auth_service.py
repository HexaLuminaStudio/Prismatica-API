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
from app.db import getDb
from app.errors import ApiError
from app.models import (
    LicenseCode,
    RechargeRecord,
    RefreshToken,
    UserAccount,
    UserBalance,
    UserDevice,
)
from app.models.user import (
    ActivationCode,
    InviteCode,
    RechargeCode,
    TrialCode,
    UserTier,
)
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


def _toNaive(dt: datetime) -> datetime:
    """统一为 naive UTC(与 MySQL TIMESTAMP 比较)。"""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


# 存量激活码 userType 为中文标签,映射为 UserTier 枚举值
_ACTIVATION_TIER_MAP = {
    "正式用户": UserTier.PAID.value,
    "正式版": UserTier.PAID.value,
    "pro": UserTier.PAID.value,
    "paid": UserTier.PAID.value,
    "普通用户": UserTier.BETA.value,
    "内测用户": UserTier.BETA.value,
    "beta": UserTier.BETA.value,
    "trial": UserTier.TRIAL.value,
}


def _mapActivationTier(userType: str) -> str:
    """将存量激活码的中文 userType 映射为 UserTier 值。"""
    if not userType:
        return UserTier.BETA.value
    mapped = _ACTIVATION_TIER_MAP.get(str(userType).strip().lower())
    if mapped:
        return mapped
    try:
        return UserTier(userType).value
    except ValueError:
        return UserTier.BETA.value


def _genRefreshTokenId() -> str:
    # 表 token_id CHAR(36),用 token_hex(16) → 32 hex 字符(预留 4 字符)
    return secrets.token_hex(16)


def _ensureDevice(
    db: Session, userId: str, deviceId: str, deviceName: str, platform: str
) -> None:
    """upsert device(若不存在则创建)。

    2026-08-07:UserDevice alias 到 IdentityDevice,主键是 BIGINT id,
    这里改为按 deviceId 字段查重以保持 upsert 语义。
    """
    from sqlalchemy import select

    from app.models.identity import IdentityDevice as _IdentityDevice

    device = db.execute(
        select(_IdentityDevice).where(_IdentityDevice.deviceId == deviceId)
    ).scalar_one_or_none()
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
            expireAt=user.expireAt,
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
    """解码 + 验签 + 反序列化为 Pydantic 模型(2026-08-06 降级)。

    兼容两种入参格式:
        1. base64(json+sig) — 完整 signed payload,客户端直接 redeem
        2. 明文 INV-/TRY-/RCH- 码 — admin 后台签发后给用户的明文形式,
           走 license_codes 表反查元数据路径,**该路径不验签**
           (因为明文码本身已是可信的,服务器自己用 sha256 找到了它)

    Returns:
        (kind: str, model, signature)
    """
    data = _decodeRawCode(rawCode)
    signature = data.get("signature")
    payloadWithoutSig = {k: v for k, v in data.items() if k != "signature"}

    if signature:
        # base64 signed payload 路径 — 必须通过验签
        if not hmacUtil.verifyPayload(payloadWithoutSig, signature):
            raise ApiError("INVALID_CODE", "凭证签名校验失败")
    else:
        # 明文码降级路径 — 仅允许明文码 + 表反查成功的结果无 signature
        # 用 codeBody 形如 INV-/TRY-/RCH- 二次确认
        raw = (rawCode or "").strip()
        if not any(raw.startswith(p) for p in ("INV-", "TRY-", "RCH-")):
            raise ApiError("INVALID_CODE", "凭证格式错误")

    codeField = data.get("code", "")
    prefix = codeField.split("-", 1)[0] if codeField else ""

    try:
        if prefix == "INV":
            return "invite", InviteCode.model_validate(payloadWithoutSig)
        if prefix == "TRY":
            return "trial", TrialCode.model_validate(payloadWithoutSig)
        if prefix == "RCH":
            return "recharge", RechargeCode.model_validate(payloadWithoutSig)
    except Exception as e:
        raise ApiError("INVALID_CODE", f"凭证字段不合法: {e}") from e

    # 存量激活码:无 code 字段,含 deviceCode / validityPeriod / userType
    if not codeField and (data.get("validityPeriod") or data.get("deviceCode")):
        try:
            return "activation", ActivationCode.model_validate(payloadWithoutSig)
        except Exception as e:
            raise ApiError("INVALID_CODE", f"激活码字段不合法: {e}") from e

    if not codeField:
        raise ApiError("INVALID_CODE", "凭证缺少 code 字段")
    raise ApiError("INVALID_CODE", f"未知凭证类型: {prefix}")


def _decodeRawCode(rawCode: str) -> dict:
    """解码 rawCode,自动兼容明文码(2026-08-06 新增;2026-08-06 降级)。

    两条路径:
        1. base64 signed payload — 客户端直接 redeem(payload 已含 code + 签名)
        2. 明文 INV-/TRY-/RCH- 码 — admin 后台签发后给用户的明文形式;
           通过 sha256(code) 在 license_codes 表里反查元数据,
           直接用表里的元数据 + 明文 codeBody 构造 payload(不再二次 decodeSignedCode)

    降级理由:若 license_codes.raw_code_signature 与历史旧格式不兼容,
    直接二次 base64+JSON 解析会触发 Incorrect padding,redeem 失败;
    改为不再依赖 raw_code_signature,只信任 license_codes 元数据列。
    """
    try:
        return hmacUtil.decodeSignedCode(rawCode)
    except ValueError:
        # 不是 base64 signed payload,可能是 admin 给的明文码
        pass

    rawCode = (rawCode or "").strip()
    # 仅尝试"明文 INV/TRY/RCH-XX-..."这种特征;长度阈值避免误匹配
    if not rawCode or not any(rawCode.startswith(p) for p in ("INV-", "TRY-", "RCH-")):
        raise ApiError(
            "INVALID_CODE",
            "码无效或已损坏: 凭证格式错误",
        )

    # 通过 codeHash 反查 license_codes 表的元数据,
    # 直接用表列构造 payload,不再走 raw_code_signature。
    from app.models import LicenseCode  # 局部导入避免循环

    codeHash = hmacUtil.hashCode(rawCode)
    with getDb() as db:
        row = db.get(LicenseCode, codeHash)
        if row is None:
            raise ApiError(
                "INVALID_CODE",
                "码无效或已损坏: 未找到凭证或凭证已下线",
            )
        if row.codeKind == "activation":
            # 存量激活码不含 codeBody 字段,走老路径(从 raw_code_signature 反查)
            if not row.rawCodeSignature:
                raise ApiError(
                    "INVALID_CODE",
                    "码无效或已损坏: 激活码凭证缺失",
                )
            try:
                return hmacUtil.decodeSignedCode(row.rawCodeSignature)
            except ValueError as e:
                raise ApiError(
                    "INVALID_CODE",
                    f"码无效或已损坏: 服务端凭证异常 ({e})",
                ) from e

        # INV/TRY/RCH:用表元数据直接构造 payload
        if row.expireAt is None:
            raise ApiError(
                "INVALID_CODE",
                "码无效或已损坏: 凭证缺失有效期",
            )
        from app.models.user import UserTier

        issuedAt = (
            row.issuedAt
            if row.issuedAt is not None
            else datetime.now(UTC).replace(tzinfo=None)
        )
        expireAt = row.expireAt
        payload: dict = {
            "code": rawCode,
            "issuedAt": issuedAt.isoformat(),
            "expireAt": expireAt.isoformat(),
            "version": 1,
        }
        if row.codeKind == "invite":
            payload.update(
                {
                    "maxUses": 1,
                    "grantedBalance": int(row.grantedBalance or 0),
                    "grantedDays": int(row.grantedDays or 0),
                    "tier": row.tier or UserTier.BETA.value,
                }
            )
        elif row.codeKind == "trial":
            payload.update(
                {
                    "maxUses": 1,
                    "grantedBalance": int(row.grantedBalance or 0),
                    "grantedDays": int(row.grantedDays or 0),
                    "tier": UserTier.TRIAL.value,
                }
            )
        elif row.codeKind == "recharge":
            payload.update(
                {"amount": int(row.amount or 0), "note": "admin issued"}
            )
        return payload


def redeemCode(
    db: Session,
    rawCode: str,
    deviceId: str,
    deviceName: str = "",
    platform: str = "",
    displayName: str = "内测用户",
    clientIp: str | None = None,
) -> RedeemResponse:
    """统一兑换入口(自动识别 INV/TRY/RCH/ACTIVATION)。

    身份语义(设备绑定 + 幂等恢复):
        - 同设备重新激活(任意新码)→ 复用该设备已绑定的 userId,赠予合并
        - 重输已消费的同一凭证 → 复用原用户(跨设备恢复登录),不再重复赠予
        - 全新设备 + 全新凭证 → 新建用户(正式 uuid4)
    """
    kind, model = _parseAndVerify(rawCode)
    codeHash = hmacUtil.hashCode(rawCode)
    now = datetime.now(UTC).replace(tzinfo=None)  # 与 MySQL TIMESTAMP 比较保持 naive

    # ============ 充值码:严格一次性,需已绑定用户 ============
    if kind == "recharge":
        if not model.amount or model.amount <= 0:
            raise ApiError("INVALID_CODE", "充值码金额无效")

        existing = db.get(LicenseCode, codeHash)
        if existing is not None and existing.status == "consumed":
            raise ApiError("ALREADY_USED", "该充值码已被使用")
        expireAt = _toNaive(model.expireAt)
        if expireAt < now:
            raise ApiError("EXPIRED", "该充值码已过期")

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
                    LicenseCode(
                        codeHash=codeHash,
                        codeKind="recharge",
                        status="consumed",
                        amount=model.amount,
                        issuedBy="system",
                        issuedAt=getattr(model, "issuedAt", now),
                        expireAt=expireAt,
                        consumedAt=now,
                        consumedByUserId=userId,
                        consumedIp=clientIp,
                    )
                )
            else:
                existing.status = "consumed"
                existing.consumedAt = now
                existing.consumedByUserId = userId
                existing.consumedIp = clientIp
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

    # ============ 邀请 / 体验 / 激活码:设备绑定 + 幂等恢复 ============
    existing = db.get(LicenseCode, codeHash)
    codeConsumed = existing is not None and existing.status == "consumed"
    consumedUserId = existing.consumedByUserId if (codeConsumed and existing is not None) else None

    # 码自身有效期(激活码用 validityPeriod 日期,其余用 expireAt)
    if kind == "activation":
        validity = getattr(model, "validityPeriod", None)
        codeExpire: datetime | None = None
        if validity:
            try:
                codeExpire = datetime.strptime(validity, "%Y-%m-%d").replace(
                    hour=23, minute=59, second=59
                )
            except ValueError:
                raise ApiError("INVALID_CODE", "激活码有效期字段不合法") from None
            if codeExpire < now:
                raise ApiError("EXPIRED", "该激活码已过期")
    else:
        codeExpire = _toNaive(model.expireAt)
        if codeExpire < now:
            raise ApiError("EXPIRED", "该凭证已过期")

    # 用户解析优先级:设备绑定 > 已消费码绑定 > 新建(uuid4)
    # 2026-08-07:UserAccount 已是 IdentityUser 的 alias,主键是 BIGINT id,
    # 而旧 redeem 路径用 userId (String(36) UUID) 做关联。这里改为按
    # userId 字段查(SQLAlchemy 2.x 友好);M6 升级后这里的 userId 字段会被
    # 删除,统一走 BIGINT id。
    from sqlalchemy import select

    from app.models.identity import IdentityDevice
    from app.models.identity import User as IdentityUser

    device = db.execute(
        select(IdentityDevice).where(IdentityDevice.deviceId == deviceId)
    ).scalar_one_or_none()
    user = None
    if device is not None:
        user = db.execute(
            select(IdentityUser).where(IdentityUser.userId == device.userId)
        ).scalar_one_or_none()
    if user is None and consumedUserId:
        user = db.execute(
            select(IdentityUser).where(IdentityUser.userId == consumedUserId)
        ).scalar_one_or_none()
    if user is None:
        tier = (
            _mapActivationTier(getattr(model, "userType", "正式用户"))
            if kind == "activation"
            else (model.tier.value if hasattr(model.tier, "value") else str(model.tier))
        )
        user = UserAccount(
            userId=_newUserId(),
            displayName=displayName or getattr(model, "code", "") or "内测用户",
            tier=tier,
            status="active",
            activatedAt=now,
            expireAt=None,
        )
        db.add(user)
        db.flush()
    elif user.status != "active":
        raise ApiError("ALREADY_AUTHENTICATED", "账号状态异常,请先注销后再兑换")

    # 2026-08-07(M6):为 P0-A 订阅表准备 BIGINT user_id 映射。
    # 老 redeem 路径创建的用户主键是 String(36) UUID,而 subscription / balance_ledger
    # 等 P0-A 表需要 BIGINT。这里查询或创建对应的 IdentityUser 行,把 BIGINT id
    # 用作 subscription.userId。
    from app.models.identity import User as IdentityUser
    bigintUser = db.execute(
        select(IdentityUser).where(IdentityUser.userId == user.userId)
    ).scalar_one_or_none()
    if bigintUser is None:
        bigintUser = IdentityUser(
            userId=user.userId,
            email=None,  # 老路径无邮箱
            passwordHash=None,  # 老路径无密码
            displayName=user.displayName,
            tier=user.tier or "free",
            status="active",
            activatedAt=now,
        )
        db.add(bigintUser)
        db.flush()
    bigintUserId = int(bigintUser.id)

    # 赠予与有效期:仅当凭证尚未消费(已消费只做幂等恢复,不重复发余额)
    balance = _ensureBalance(db, user.userId)
    if not codeConsumed:
        if kind == "activation":
            grantedBalance = 0
            grantExpire = codeExpire
        else:
            grantedBalance = model.grantedBalance
            grantExpire = now + timedelta(days=model.grantedDays)

        beforeBalance = balance.balance
        if grantedBalance > 0:
            balance.balance += grantedBalance
            balance.totalRecharged += grantedBalance
            balance.version += 1
            db.flush()
            _writeRechargeRecord(
                db,
                userId=user.userId,
                amount=grantedBalance,
                source=f"{kind}_grant",
                balanceBefore=beforeBalance,
                balanceAfter=balance.balance,
                codeHash=codeHash,
                operatorNote=f"{kind} {getattr(model, 'code', '')}",
            )

        if grantExpire is not None and (user.expireAt is None or grantExpire > user.expireAt):
            user.expireAt = grantExpire

        # 2026-08-07(M6):INV / RCH / TRY 路径额外创建 P0-A subscription 行 +
        # 写 balance_ledger,以便 P0-A 前端能查询 /v1/account/subscriptions。
        if kind in {"invite", "trial", "recharge"}:
            from app.models.code_redemption import CodeRedemption
            from app.services.subscription_service import (
                redeemInviteCode,
                redeemRechargeCode,
                redeemTrialCode,
            )

            # 2026-08-07:license_codes 当前主键是 code_hash(String),
            # CodeRedemption.code_hash FK 直接用它;M6 升级 license_codes 后切到 id。
            # 注意:tier 升级策略不直接修改 user.tier,以保持 M3 之前
            # redeem 测试断言(tier 仍由 _mapActivationTier 决定);
            # 真正的 tier 升级交由 /v1/account/me 在返回时根据活跃 subscription 推断。
            if kind == "invite":
                redeemInviteCode(
                    db,
                    userId=bigintUserId,
                    grantedBalance=grantedBalance,
                    grantedDays=int(model.grantedDays or 0),
                    codeId=0,
                    clientIp=clientIp,
                )
            elif kind == "trial":
                redeemTrialCode(
                    db,
                    userId=bigintUserId,
                    grantedBalance=grantedBalance,
                    grantedDays=int(model.grantedDays or 0),
                    codeId=0,
                )
            elif kind == "recharge":
                redeemRechargeCode(
                    db,
                    userId=bigintUserId,
                    amount=grantedBalance,
                    codeId=0,
                )

            # 写 code_redemptions(同事务;code_hash 是 FK 必填)
            try:
                db.add(
                    CodeRedemption(
                        codeHash=codeHash,
                        userId=bigintUserId,
                        amountGranted=grantedBalance,
                        clientIp=clientIp,
                    )
                )
                db.flush()
            except IntegrityError:
                # 已存在(code_hash + user_id 唯一) — 跳过
                db.rollback()

        # 写幂等表(2026-08-06:若 license_codes 已有 admin 预写的行(明文码路径)
        # 则就地 UPDATE,不重复 INSERT,避免与 admin 签发的 rawCodeSignature 行主键冲突)
        try:
            if existing is None:
                db.add(
                    LicenseCode(
                        codeHash=codeHash,
                        codeKind=kind,
                        status="consumed",
                        issuedBy="system",
                        issuedAt=getattr(model, "issuedAt", now),
                        expireAt=codeExpire,
                        consumedAt=now,
                        consumedByUserId=user.userId,
                        consumedIp=clientIp,
                    )
                )
            else:
                # 明文码路径:admin 已存 active 行,直接翻转为 consumed
                existing.status = "consumed"
                existing.consumedAt = now
                existing.consumedByUserId = user.userId
                existing.consumedIp = clientIp
            db.flush()
        except IntegrityError:
            db.rollback()
            raise ApiError("ALREADY_USED", "该凭证已被使用") from None
    else:
        logger.info(
            f"[Auth] {kind} 幂等恢复 user={user.userId}(凭证已消费,跳过赠予)"
        )

    _ensureDevice(db, user.userId, deviceId, deviceName, platform)
    refresh = _issueRefreshToken(db, user.userId, deviceId)
    db.commit()

    logger.info(
        f"[Auth] {kind} 激活成功 user={user.userId} device={deviceId[:8]}... "
        f"balance={balance.balance}"
    )
    return _buildRedeemResponse(
        db,
        user,
        balance,
        deviceId,
        tier=user.tier,
        refreshTokenId=refresh.tokenId,
        mode=kind,
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
