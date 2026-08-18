"""P0-A code_redemptions — 兑换码使用记录。

每次成功 redeem 都会写一条记录,作为「谁在何时使用了哪个码」的可追溯轨迹。
与 license_codes(码元数据 + status)不同:本表是事实表,只追加。

唯一约束:(code_id, user_id)— 同一用户对同一码只能产生一条成功记录。
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class CodeRedemption(Base):
    __tablename__ = "code_redemptions"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    codeId: Mapped[int] = mapped_column(
        "code_id",
        BIGINT_ID,
        ForeignKey("license_codes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    userId: Mapped[int] = mapped_column(
        "user_id", BIGINT_ID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    subscriptionId: Mapped[int | None] = mapped_column(
        "subscription_id", BIGINT_ID, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    amountGranted: Mapped[int] = mapped_column("amount_granted", BigInteger, nullable=False, default=0)
    clientIp: Mapped[str | None] = mapped_column("client_ip", String(64), nullable=True)
    redeemedAt: Mapped[datetime] = mapped_column(
        "redeemed_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        Index("uk_code_redemptions_code_user", "code_id", "user_id", unique=True),
        Index("idx_code_redemptions_user_time", "user_id", "redeemed_at"),
    )


__all__ = ["CodeRedemption"]
