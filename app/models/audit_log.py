"""audit_logs — 审计日志(所有 admin 行为)。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditLog(Base):
    """审计日志。"""

    __tablename__ = "audit_logs"

    auditId: Mapped[int] = mapped_column(
        "audit_id", BigInteger, primary_key=True, autoincrement=True
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    targetUser: Mapped[str | None] = mapped_column(
        "target_user", String(36), nullable=True
    )
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, server_default=func.current_timestamp()
    )

    __table_args__ = (Index("idx_audit_actor_time", "actor", "created_at"),)


__all__ = ["AuditLog"]
