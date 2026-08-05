"""recharge_records — 充值/赠送流水。"""
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RechargeRecord(Base):
    """充值/赠送流水。"""

    __tablename__ = "recharge_records"

    recordId: Mapped[str] = mapped_column("record_id", String(36), primary_key=True)
    userId: Mapped[str] = mapped_column(
        "user_id", String(36), ForeignKey("user_accounts.user_id"), nullable=False
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    codeHash: Mapped[str | None] = mapped_column("code_hash", String(64), nullable=True)
    operatorNote: Mapped[str] = mapped_column(
        "operator_note", String(256), nullable=False, default=""
    )
    balanceBefore: Mapped[int] = mapped_column(
        "balance_before", BigInteger, nullable=False, default=0
    )
    balanceAfter: Mapped[int] = mapped_column(
        "balance_after", BigInteger, nullable=False, default=0
    )
    expireAt: Mapped[datetime | None] = mapped_column(
        "expire_at", DateTime, nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, server_default=func.current_timestamp()
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="chk_recharge_amount"),
        Index("idx_recharge_records_user", "user_id", "created_at"),
    )


__all__ = ["RechargeRecord"]
