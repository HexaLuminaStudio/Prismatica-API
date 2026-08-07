"""audit_logs — 审计日志(所有 admin / user 关键行为)。

2026-08-07 改造:补 target_type / target_id / request_id 字段(对齐 schema.sql),
便于按资源类型 / 请求链路聚合审计。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


# 2026-08-07:audit_id 在 sqlite 测试环境用 Integer 走 autoincrement;
# 生产 MySQL 8 仍走 BIGINT(BigInteger().with_variant(Integer, "sqlite"))。
_AUDIT_ID = BigInteger().with_variant(Integer, "sqlite")


class AuditLog(Base):
    """审计日志。"""

    __tablename__ = "audit_logs"

    auditId: Mapped[int] = mapped_column(
        "audit_id", _AUDIT_ID, primary_key=True, autoincrement=True
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    targetType: Mapped[str | None] = mapped_column("target_type", String(32), nullable=True)
    targetId: Mapped[str | None] = mapped_column("target_id", String(64), nullable=True)
    targetUser: Mapped[str | None] = mapped_column(
        "target_user", String(64), nullable=True
    )
    requestId: Mapped[str | None] = mapped_column("request_id", String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, server_default=func.current_timestamp()
    )

    __table_args__ = (
        Index("idx_audit_actor_time", "actor", "created_at"),
        Index("idx_audit_target", "target_type", "target_id", "created_at"),
    )


__all__ = ["AuditLog"]
