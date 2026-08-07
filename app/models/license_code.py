"""license_codes — 兑换码持久化。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, Integer, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LicenseCode(Base):
    """兑换码。"""

    __tablename__ = "license_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    codeHash: Mapped[str] = mapped_column("code_hash", String(64), nullable=False, unique=True)
    codeKind: Mapped[str] = mapped_column("code_kind", String(8), nullable=False)
    status: Mapped[str] = mapped_column("status", String(16), nullable=False, default="active")
    planCode: Mapped[str | None] = mapped_column("plan_code", String(32), nullable=True)
    periodMonths: Mapped[int | None] = mapped_column("period_months", SmallInteger, nullable=True)
    trialDays: Mapped[int | None] = mapped_column("trial_days", SmallInteger, nullable=True)
    monthlyQuota: Mapped[int | None] = mapped_column("monthly_quota", Integer, nullable=True)
    amount: Mapped[int | None] = mapped_column("amount", BigInteger, nullable=True)
    maxUses: Mapped[int] = mapped_column("max_uses", Integer, nullable=False, default=1)
    usedCount: Mapped[int] = mapped_column("used_count", Integer, nullable=False, default=0)
    issuedBy: Mapped[str | None] = mapped_column("issued_by", String(36), nullable=True)
    note: Mapped[str] = mapped_column("note", String(255), nullable=False, default="")
    issuedAt: Mapped[datetime] = mapped_column("issued_at", DateTime, server_default=func.current_timestamp())
    expiresAt: Mapped[datetime | None] = mapped_column("expires_at", DateTime, nullable=True)
    revokedAt: Mapped[datetime | None] = mapped_column("revoked_at", DateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column("created_at", DateTime, server_default=func.current_timestamp())

    __table_args__ = (
        CheckConstraint("code_kind IN ('INV','RCH','TRY')", name="chk_license_codes_kind"),
        CheckConstraint("status IN ('active','exhausted','revoked','expired')", name="chk_license_codes_status"),
        Index("idx_license_codes_kind_status", "code_kind", "status"),
        Index("idx_license_codes_expires_at", "status", "expires_at"),
    )


__all__ = ["LicenseCode"]
