"""user_accounts / user_devices / user_balances 三表(用户域)。

设计要点:
    - user_id = CHAR(36) UUID
    - user_balances 通过 version 字段实现乐观锁;preauth/settle 用 SELECT ... FOR UPDATE 行锁
    - user_devices 一机一档(device_id 唯一),last_seen_at 心跳更新
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class UserAccount(Base):
    """用户账户主表。"""

    __tablename__ = "user_accounts"

    userId: Mapped[str] = mapped_column("user_id", String(36), primary_key=True)
    displayName: Mapped[str] = mapped_column("display_name", String(64), nullable=False)
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="beta")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    activatedAt: Mapped[datetime] = mapped_column("activated_at", DateTime, nullable=False)
    expireAt: Mapped[datetime | None] = mapped_column("expire_at", DateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, server_default=func.current_timestamp()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at",
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    balance: Mapped[UserBalance] = relationship(
        "UserBalance",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (Index("idx_user_accounts_status", "status"),)


class UserDevice(Base):
    """用户设备(多设备登录)。"""

    __tablename__ = "user_devices"

    deviceId: Mapped[str] = mapped_column("device_id", String(36), primary_key=True)
    userId: Mapped[str] = mapped_column(
        "user_id", String(36), ForeignKey("user_accounts.user_id"), nullable=False
    )
    deviceName: Mapped[str] = mapped_column(
        "device_name", String(128), nullable=False, default=""
    )
    platform: Mapped[str] = mapped_column(
        "platform", String(32), nullable=False, default=""
    )
    firstSeenAt: Mapped[datetime] = mapped_column(
        "first_seen_at", DateTime, server_default=func.current_timestamp()
    )
    lastSeenAt: Mapped[datetime] = mapped_column(
        "last_seen_at",
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (Index("idx_user_devices_user", "user_id", "last_seen_at"),)


class UserBalance(Base):
    """用户余额(1:1 行锁粒度)。"""

    __tablename__ = "user_balances"

    userId: Mapped[str] = mapped_column(
        "user_id", String(36), ForeignKey("user_accounts.user_id"), primary_key=True
    )
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    frozenBalance: Mapped[int] = mapped_column(
        "frozen_balance", BigInteger, nullable=False, default=0
    )
    totalSpent: Mapped[int] = mapped_column(
        "total_spent", BigInteger, nullable=False, default=0
    )
    totalRecharged: Mapped[int] = mapped_column(
        "total_recharged", BigInteger, nullable=False, default=0
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at",
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    user: Mapped[UserAccount] = relationship("UserAccount", back_populates="balance")

    __table_args__ = (
        CheckConstraint("balance >= 0", name="chk_user_balances_balance"),
        CheckConstraint("frozen_balance >= 0", name="chk_user_balances_frozen"),
    )


__all__ = ["UserAccount", "UserDevice", "UserBalance"]
