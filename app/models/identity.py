"""P0-A BIGINT 用户身份域 ORM。

2026-08-07 改造:
    IdentityBase 现在直接别名自 app.db.Base,以便所有 P0-A 表(users /
    user_devices / user_balance / subscriptions / balance_ledger 等)共享同一
    个 SQLAlchemy metadata,ForeignKey 跨表引用能正确解析(早期 IdentityBase
    是独立 DeclarativeBase,导致订阅表在引用 users 时报 NoReferencedTableError)。
    测试 fixture 也只需要一次 Base.metadata.create_all(engine) 即可。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# 2026-08-07:IdentityBase 是 Base 的别名,以便新旧模型共用 metadata。
# 历史代码仍可写 `from app.models.identity import IdentityBase`,
# 但实际拿到的是共享 Base,创建表时统一走 Base.metadata.create_all。
IdentityBase = Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class User(IdentityBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    # 2026-08-07:旧兑换登录路径(auth_service.py)用 userId=uuid4 字符串
    # 构造 User。这里把 userId 作为 String(36) 冗余字段保留,M6 之后
    # 旧路径下线时可删除。P0-A 新代码一律用 id (BIGINT)。
    userId: Mapped[str | None] = mapped_column("user_id", String(36), nullable=True, unique=True)
    # 2026-08-07:旧 redeem 路径(auth_service.redeemCode)创建 User 时
    # 不写 email / passwordHash,只设 userId + displayName + tier + status。
    # 这些字段在 M3 之前是「设备绑定的匿名账号」,现在共享同一张 users 表,
    # 必须让 email / passwordHash nullable 以兼容。M3 之后所有新用户都是
    # 邮箱密码账号,这两列非空。
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, unique=True)
    passwordHash: Mapped[str | None] = mapped_column("password_hash", String(255), nullable=True)
    displayName: Mapped[str] = mapped_column("display_name", String(64), nullable=False, default="")
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    failedLoginCount: Mapped[int] = mapped_column("failed_login_count", Integer, nullable=False, default=0)
    lockedUntil: Mapped[datetime | None] = mapped_column("locked_until", DateTime, nullable=True)
    emailVerified: Mapped[bool] = mapped_column("email_verified", nullable=False, default=False)
    deletedAt: Mapped[datetime | None] = mapped_column("deleted_at", DateTime, nullable=True)
    activatedAt: Mapped[datetime | None] = mapped_column("activated_at", DateTime, nullable=True)
    expireAt: Mapped[datetime | None] = mapped_column("expire_at", DateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        Index("idx_users_status_tier", "status", "tier"),
        Index("idx_users_created_at", "created_at"),
    )

    # ------------------------------------------------------------------
    # 字段别名:旧兑换登录路径(auth_service.py / admin_user_service.py)
    # 仍以 userId=... 构造 User,这里把它写到 userId(String) 字段。
    # ------------------------------------------------------------------
    def __init__(self, *args, **kwargs):
        # 旧代码传 userId=uuid4 时,写到 userId(String),不让它污染 id
        if "userId" in kwargs and "id" not in kwargs:
            kwargs["userId"] = kwargs.pop("userId")
        super().__init__(*args, **kwargs)


class IdentityDevice(IdentityBase):
    __tablename__ = "user_devices"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    # 2026-08-07:userId 暂用 String(36) 兼容旧 redeem 路径
    # (auth_service._ensureDevice 直接传 deviceId=str),M6 升级后切回 BIGINT。
    userId: Mapped[str] = mapped_column(
        "user_id", String(36), nullable=False
    )
    deviceId: Mapped[str] = mapped_column("device_id", String(64), nullable=False)
    deviceName: Mapped[str] = mapped_column("device_name", String(128), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    firstSeenAt: Mapped[datetime] = mapped_column(
        "first_seen_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    lastSeenAt: Mapped[datetime] = mapped_column(
        "last_seen_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )
    revokedAt: Mapped[datetime | None] = mapped_column("revoked_at", DateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "device_id", name="uk_user_devices_user_device"),
        Index("idx_user_devices_user_status", "user_id", "status"),
        Index("idx_user_devices_last_seen", "last_seen_at"),
    )


class IdentityBalance(IdentityBase):
    __tablename__ = "user_balance"

    # 2026-08-07:旧 redeem 路径写入 user_balance 时 user_id 是 uuid 字符串;
    # 新 P0-A 路径是 BIGINT。这里保留 String(36) 主键以便旧路径继续可用;
    # 新代码 M6 升级后会切回 BIGINT。
    userId: Mapped[str] = mapped_column(
        "user_id", String(36), primary_key=True
    )
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    lifetimeGrant: Mapped[int] = mapped_column("lifetime_grant", BigInteger, nullable=False, default=0)
    lifetimeConsumed: Mapped[int] = mapped_column("lifetime_consumed", BigInteger, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at",
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    # ------------------------------------------------------------------
    # 字段别名:旧 auth_service.py 仍使用 totalRecharged / totalSpent /
    # frozenBalance(P0-A 用 lifetime_* 命名),用 property 兼容以避免
    # 大改 M3 之前 redeem 路径。
    # ------------------------------------------------------------------
    @property
    def totalRecharged(self) -> int:
        return int(self.lifetimeGrant or 0)

    @totalRecharged.setter
    def totalRecharged(self, value: int) -> None:
        self.lifetimeGrant = int(value or 0)

    @property
    def frozenBalance(self) -> int:
        return int(self.reserved or 0)

    @frozenBalance.setter
    def frozenBalance(self, value: int) -> None:
        self.reserved = int(value or 0)

    @property
    def totalSpent(self) -> int:
        return int(self.lifetimeConsumed or 0)

    @totalSpent.setter
    def totalSpent(self, value: int) -> None:
        self.lifetimeConsumed = int(value or 0)


class PasswordResetToken(IdentityBase):
    __tablename__ = "password_reset_tokens"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    userId: Mapped[int] = mapped_column(
        "user_id", BIGINT_ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    tokenHash: Mapped[str] = mapped_column("token_hash", String(64), nullable=False, unique=True)
    expiresAt: Mapped[datetime] = mapped_column("expires_at", DateTime, nullable=False)
    usedAt: Mapped[datetime | None] = mapped_column("used_at", DateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (Index("idx_password_reset_user_expiry", "user_id", "expires_at"),)


__all__ = [
    "IdentityBase",
    "User",
    "IdentityDevice",
    "IdentityBalance",
    "PasswordResetToken",
]
