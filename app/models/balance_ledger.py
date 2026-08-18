"""P0-A balance_ledger — 不可变余额账本。

每个计费写操作(grant / consume / reserve / unreserve / refund / adjust)
都会在 user_balance 行变更的同时插入一条 ledger 记录。ledger 是只追加的,
不允许 UPDATE / DELETE(应用层 + DB 都不应)。

表结构(对齐 schema.sql):
    id, user_id, entry_type, amount,
    balance_delta, reserved_delta, balance_after, reserved_after,
    source, ref_type, ref_id, note, created_at

entry_type:
    grant       — 充值/订阅派发(增 balance)
    consume     — 实际消费(减 balance)
    reserve     — 预占(减 balance, 增 reserved)
    unreserve   — 释放预占(增 balance, 减 reserved)
    refund      — 退款(增 balance)
    adjust      — 管理员手动调整
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
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class BalanceLedger(Base):
    __tablename__ = "balance_ledger"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    userId: Mapped[int] = mapped_column(
        "user_id", BIGINT_ID, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    entryType: Mapped[str] = mapped_column("entry_type", String(16), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balanceDelta: Mapped[int] = mapped_column("balance_delta", BigInteger, nullable=False, default=0)
    reservedDelta: Mapped[int] = mapped_column("reserved_delta", BigInteger, nullable=False, default=0)
    balanceAfter: Mapped[int] = mapped_column("balance_after", BigInteger, nullable=False)
    reservedAfter: Mapped[int] = mapped_column("reserved_after", BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    refType: Mapped[str | None] = mapped_column("ref_type", String(32), nullable=True)
    refId: Mapped[str | None] = mapped_column("ref_id", String(64), nullable=True)
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        Index("idx_balance_ledger_user_time", "user_id", "created_at"),
        Index("idx_balance_ledger_source_ref", "source", "ref_type", "ref_id"),
    )


__all__ = ["BalanceLedger"]
