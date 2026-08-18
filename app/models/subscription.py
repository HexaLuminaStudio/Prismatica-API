"""P0-A 订阅表 ORM。

对齐 schema:
    subscriptions(
        id, user_id, plan_code, status,
        started_at, current_period_start, current_period_end, expires_at,
        next_grant_at, auto_renew, monthly_quota,
        created_at, updated_at
    )

status 状态机:
    active    — 正常订阅中,按 current_period_end / next_grant_at 续期
    past_due  — 续费失败 / 余额不足,等待重新扣款
    canceled  — 用户主动取消,到期后转 expired
    expired   — 周期已过,失效
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    userId: Mapped[int] = mapped_column(
        "user_id", BIGINT_ID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    planCode: Mapped[str] = mapped_column("plan_code", String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    startedAt: Mapped[datetime] = mapped_column("started_at", DateTime, nullable=False)
    currentPeriodStart: Mapped[datetime] = mapped_column("current_period_start", DateTime, nullable=False)
    currentPeriodEnd: Mapped[datetime] = mapped_column("current_period_end", DateTime, nullable=False)
    expiresAt: Mapped[datetime] = mapped_column("expires_at", DateTime, nullable=False)
    nextGrantAt: Mapped[datetime | None] = mapped_column("next_grant_at", DateTime, nullable=True)
    autoRenew: Mapped[bool] = mapped_column("auto_renew", Boolean, nullable=False, default=False)
    monthlyQuota: Mapped[int] = mapped_column("monthly_quota", Integer, nullable=False, default=0)
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
        Index("idx_subscriptions_user_status", "user_id", "status"),
        Index("idx_subscriptions_grant_due", "status", "next_grant_at"),
        Index("idx_subscriptions_expiry", "status", "expires_at"),
    )


__all__ = ["Subscription"]
