"""license_codes_seen — 凭证码全局幂等表。

PK = sha256(code) hex;充值成功后写入 recharge_user_id / recharge_amount。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Enum, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LicenseCodeSeen(Base):
    """凭证码已用记录(全局幂等)。"""

    __tablename__ = "license_codes_seen"

    codeHash: Mapped[str] = mapped_column("code_hash", String(64), primary_key=True)
    codeKind: Mapped[str] = mapped_column(
        "code_kind", Enum("invite", "trial", "recharge"), nullable=False
    )
    issuedAt: Mapped[datetime | None] = mapped_column(
        "issued_at", DateTime, nullable=True
    )
    consumedAt: Mapped[datetime | None] = mapped_column(
        "consumed_at", DateTime, nullable=True
    )
    consumedByUserId: Mapped[str | None] = mapped_column(
        "consumed_by_user_id", String(36), nullable=True
    )
    consumeIp: Mapped[str | None] = mapped_column(
        "consume_ip", String(64), nullable=True
    )
    rechargeUserId: Mapped[str | None] = mapped_column(
        "recharge_user_id", String(36), nullable=True
    )
    rechargeAmount: Mapped[int | None] = mapped_column(
        "recharge_amount", Integer, nullable=True
    )
    expireAt: Mapped[datetime | None] = mapped_column(
        "expire_at", DateTime, nullable=True
    )

    __table_args__ = (
        Index("idx_codes_seen_user", "consumed_by_user_id", "consumed_at"),
        Index("idx_codes_seen_kind", "code_kind", "expire_at"),
    )


__all__ = ["LicenseCodeSeen"]
