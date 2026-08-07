"""新 MySQL refresh_tokens 表的 P0-A 映射。

2026-08-07 改造:TokenBase 改用共享 Base,以便所有 P0-A 表都注册在同一个
metadata,跨表 FK / 测试 fixture 统一处理。保留 TokenBase 别名以兼容历史 import。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

TokenBase = Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class StoredRefreshToken(TokenBase):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("idx_refresh_tokens_user_expiry", "user_id", "expires_at"),
        Index(
            "idx_refresh_tokens_device_active",
            "device_id",
            "revoked_at",
            "expires_at",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    jti: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    # 2026-08-07:tokenHash 暂 nullable,旧 auth_service._issueRefreshToken 路径
    # 不写 tokenHash;新 identity_auth_service 路径用 sha256(raw) 写入。
    # M6 升级后改为 nullable=False,要求所有路径都计算 tokenHash。
    tokenHash: Mapped[str | None] = mapped_column("token_hash", String(64), nullable=True, unique=True)
    # userId / deviceId 暂用 String(36) 兼容旧 auth_service 路径,
    # M6 升级后切回 BIGINT,新增的 BIGINT device 关联走 user_devices.id 整型 FK。
    userId: Mapped[str] = mapped_column("user_id", String(36), nullable=False)
    deviceId: Mapped[str] = mapped_column("device_id", String(36), nullable=False)
    expiresAt: Mapped[datetime] = mapped_column("expires_at", DateTime, nullable=False)
    revokedAt: Mapped[datetime | None] = mapped_column("revoked_at", DateTime, nullable=True)
    revokeReason: Mapped[str | None] = mapped_column("revoke_reason", String(32), nullable=True)
    replacedByJti: Mapped[str | None] = mapped_column("replaced_by_jti", String(36), nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    # ------------------------------------------------------------------
    # 旧 auth_service.py 仍以 tokenId=... 字符串构造 RefreshToken(已 alias
    # 到 StoredRefreshToken)。这里提供 jti 字段同义词以避免 M6 之前崩溃。
    # ------------------------------------------------------------------
    @property
    def tokenId(self) -> str:
        return self.jti

    @tokenId.setter
    def tokenId(self, value: str) -> None:
        self.jti = value

    def __init__(self, *args, **kwargs):
        if "tokenId" in kwargs and "jti" not in kwargs:
            kwargs["jti"] = kwargs.pop("tokenId")
        super().__init__(*args, **kwargs)


__all__ = ["StoredRefreshToken", "TokenBase"]
