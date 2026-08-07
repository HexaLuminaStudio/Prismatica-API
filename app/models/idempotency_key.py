"""P0-A idempotency_keys — 24h 幂等键缓存。

`Idempotency-Key` 头在多种接口使用(计费、兑换等),本表把
(key, operation, user_id) 作为唯一约束,缓存首次响应(状态码 + body),
24 小时内相同请求直接返回缓存。

为何不放在 bills.idempotency_key:每种操作的幂等键作用域不同,本表可以
统一处理多种 operation 而不需要为每种资源各加唯一约束。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
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


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    userId: Mapped[int] = mapped_column(
        "user_id", BIGINT_ID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotencyKey: Mapped[str] = mapped_column("idempotency_key", String(64), nullable=False)
    requestHash: Mapped[str] = mapped_column("request_hash", String(64), nullable=False)
    responseStatus: Mapped[int | None] = mapped_column("response_status", Integer, nullable=True)
    responseBody: Mapped[dict | None] = mapped_column("response_body", JSON, nullable=True)
    resourceType: Mapped[str | None] = mapped_column("resource_type", String(32), nullable=True)
    resourceId: Mapped[str | None] = mapped_column("resource_id", String(64), nullable=True)
    expiresAt: Mapped[datetime] = mapped_column("expires_at", DateTime, nullable=False)
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
        UniqueConstraint("user_id", "operation", "idempotency_key", name="uk_idempotency_scope"),
        Index("idx_idempotency_expiry", "expires_at"),
    )


__all__ = ["IdempotencyKey"]
