"""Canonical bills 表映射。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class Bill(Base):
    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    billId: Mapped[str] = mapped_column("bill_id", String(36), nullable=False, unique=True)
    userId: Mapped[int] = mapped_column(
        "user_id", BIGINT_ID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    estimatedCost: Mapped[int] = mapped_column("estimated_cost", BigInteger, nullable=False)
    actualCost: Mapped[int | None] = mapped_column("actual_cost", BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    idempotencyKey: Mapped[str] = mapped_column("idempotency_key", String(64), nullable=False)
    requestHash: Mapped[str] = mapped_column("request_hash", String(64), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    pricingVersion: Mapped[str | None] = mapped_column("pricing_version", String(40), nullable=True)
    pricingSnapshot: Mapped[dict | None] = mapped_column("pricing_snapshot", JSON, nullable=True)
    inputTokens: Mapped[int | None] = mapped_column("input_tokens", BigInteger, nullable=True)
    outputTokens: Mapped[int | None] = mapped_column("output_tokens", BigInteger, nullable=True)
    preauthExpiresAt: Mapped[datetime] = mapped_column("preauth_expires_at", DateTime, nullable=False)
    settledAt: Mapped[datetime | None] = mapped_column("settled_at", DateTime, nullable=True)
    refundedAt: Mapped[datetime | None] = mapped_column("refunded_at", DateTime, nullable=True)
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
        UniqueConstraint("user_id", "idempotency_key", name="uk_bills_user_idempotency"),
        Index("idx_bills_user_status_time", "user_id", "status", "created_at"),
        Index("idx_bills_pending_expiry", "status", "preauth_expires_at"),
    )


__all__ = ["Bill"]
