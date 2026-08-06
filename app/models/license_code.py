"""license_codes — 凭证签发持久化(2026-08-06 重构新增)。

issued 立即入库;消费时由事务写 consumed_at / consumed_by_user_id。
明文 code 仅在签发响应里一次性返回,表内只存 sha256 hash + signed payload。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class LicenseCode(Base):
    """凭证码(issued 时立即落库)。"""

    __tablename__ = "license_codes"

    codeHash: Mapped[str] = mapped_column(
        "code_hash", String(64), primary_key=True, comment="sha256(code) hex"
    )
    codeKind: Mapped[str] = mapped_column(
        "code_kind", String(16), nullable=False, comment="invite / trial / recharge"
    )
    status: Mapped[str] = mapped_column(
        "status",
        String(16),
        nullable=False,
        default="active",
        comment="active / consumed / revoked / expired",
    )

    # invite / trial 字段
    grantedBalance: Mapped[int | None] = mapped_column(
        "granted_balance", Integer, nullable=True
    )
    grantedDays: Mapped[int | None] = mapped_column(
        "granted_days", Integer, nullable=True
    )
    tier: Mapped[str | None] = mapped_column("tier", String(16), nullable=True)

    # recharge 字段
    amount: Mapped[int | None] = mapped_column("amount", Integer, nullable=True)

    # 元数据
    issuedBy: Mapped[str] = mapped_column(
        "issued_by", String(64), nullable=False, default=""
    )
    issuedAt: Mapped[datetime] = mapped_column(
        "issued_at", DateTime, server_default=func.current_timestamp()
    )
    expireAt: Mapped[datetime | None] = mapped_column("expire_at", DateTime, nullable=True)
    consumedAt: Mapped[datetime | None] = mapped_column(
        "consumed_at", DateTime, nullable=True
    )
    consumedByUserId: Mapped[str | None] = mapped_column(
        "consumed_by_user_id", String(36), nullable=True
    )
    consumedIp: Mapped[str | None] = mapped_column(
        "consumed_ip", String(64), nullable=True
    )
    # 仅在 issue 时一次性返回的 signed payload(base64(json+sig)),
    # 供前端 client.signed_code.tryParseAnyCode() 校验。后续不可再查。
    rawCodeSignature: Mapped[str | None] = mapped_column(
        "raw_code_signature", String(512), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "code_kind IN ('invite','trial','recharge','activation')",
            name="chk_license_codes_kind",
        ),
        CheckConstraint(
            "status IN ('active','consumed','revoked','expired')",
            name="chk_license_codes_status",
        ),
        CheckConstraint("amount IS NULL OR amount > 0", name="chk_license_codes_amount"),
        Index("idx_license_codes_kind_status", "code_kind", "status"),
        Index("idx_license_codes_issued", "issued_at"),
        Index("idx_license_codes_consumed", "consumed_by_user_id", "consumed_at"),
    )


__all__ = ["LicenseCode"]
