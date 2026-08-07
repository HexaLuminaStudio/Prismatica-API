"""revoked_tokens — Access/Refresh JWT jti 吊销列表。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    userId: Mapped[int] = mapped_column("user_id", BigInteger, nullable=False)
    tokenType: Mapped[str] = mapped_column("token_type", String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="logout")
    expiresAt: Mapped[datetime] = mapped_column("expires_at", DateTime, nullable=False)
    revokedAt: Mapped[datetime] = mapped_column(
        "revoked_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        Index("idx_revoked_tokens_expiry", "expires_at"),
        Index("idx_revoked_tokens_user", "user_id", "revoked_at"),
    )


__all__ = ["RevokedToken"]
