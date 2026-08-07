"""bills — 账单流水。

status 状态机:pending → settled / refunded
幂等键:UNIQUE idempotency_key(预占时设置)

2026-08-07 改造:user_id 由 String(36) UUID 改为 BIGINT(对齐 P0-A 用户主键)。
M3 之后所有新建数据都使用 BIGINT;旧的 CHAR(36) 历史账单仅 admin 后台读
旧 SQLite 时才可能存在,迁移期保留兼容层。
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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class Bill(Base):
    """账单流水。"""

    __tablename__ = "bills"

    billId: Mapped[str] = mapped_column("bill_id", String(36), primary_key=True)
    userId: Mapped[int] = mapped_column(
        "user_id", BIGINT_ID, ForeignKey("users.id"), nullable=False
    )
    actionType: Mapped[str] = mapped_column("action_type", String(32), nullable=False)
    actionDisplayName: Mapped[str] = mapped_column(
        "action_display_name", String(64), nullable=False, default=""
    )
    estimatedCost: Mapped[int] = mapped_column(
        "estimated_cost", Integer, nullable=False, default=0
    )
    realCost: Mapped[int] = mapped_column("real_cost", Integer, nullable=False, default=0)
    resourceUsed: Mapped[int] = mapped_column(
        "resource_used", BigInteger, nullable=False, default=0
    )
    balanceBefore: Mapped[int] = mapped_column(
        "balance_before", BigInteger, nullable=False, default=0
    )
    balanceAfter: Mapped[int] = mapped_column(
        "balance_after", BigInteger, nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    taskId: Mapped[str] = mapped_column("task_id", String(36), nullable=False, default="")
    description: Mapped[str] = mapped_column(
        "description", String(256), nullable=False, default=""
    )
    idempotencyKey: Mapped[str | None] = mapped_column(
        "idempotency_key", String(36), nullable=True
    )
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, server_default=func.current_timestamp()
    )
    settledAt: Mapped[datetime | None] = mapped_column(
        "settled_at", DateTime, nullable=True
    )

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uk_bills_idem"),
        Index("idx_bills_user_status", "user_id", "status", "created_at"),
    )


__all__ = ["Bill"]
