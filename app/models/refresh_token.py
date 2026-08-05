# coding: utf-8
"""refresh_tokens — Refresh Token(允许主动 revoke)。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RefreshToken(Base):
    """Refresh Token 存储(opaque UUID)。"""

    __tablename__ = "refresh_tokens"

    tokenId: Mapped[str] = mapped_column("token_id", String(36), primary_key=True)
    userId: Mapped[str] = mapped_column(
        "user_id", String(36), ForeignKey("user_accounts.user_id"), nullable=False
    )
    deviceId: Mapped[str] = mapped_column("device_id", String(36), nullable=False)
    expiresAt: Mapped[datetime] = mapped_column("expires_at", DateTime, nullable=False)
    revokedAt: Mapped[Optional[datetime]] = mapped_column(
        "revoked_at", DateTime, nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, server_default=func.current_timestamp()
    )

    __table_args__ = (Index("idx_refresh_user", "user_id", "expires_at"),)


__all__ = ["RefreshToken"]