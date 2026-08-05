# coding: utf-8
"""管理员账号(2026-08-05 M2 B1 新增)

用于 PrismaticaAdmin 管理后台登录。bcrypt(stretch=12)哈希密码。
status: active / locked(被管理员锁定 / 超阈值登录失败)。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AdminUser(Base):
    """管理员账号表。"""

    __tablename__ = "admin_users"

    userId: Mapped[str] = mapped_column(
        "user_id", String(36), primary_key=True, comment="管理员 UUID"
    )
    username: Mapped[str] = mapped_column(
        "username", String(64), nullable=False, comment="登录用户名(唯一)"
    )
    passwordHash: Mapped[str] = mapped_column(
        "password_hash", String(255), nullable=False, comment="bcrypt(stretch=12) 哈希"
    )
    role: Mapped[str] = mapped_column(
        "role", String(32), nullable=False, default="admin", comment="角色:本期固定 admin"
    )
    status: Mapped[str] = mapped_column(
        "status",
        String(16),
        nullable=False,
        default="active",
        comment="active / locked",
    )
    lastLoginAt: Mapped[datetime | None] = mapped_column(
        "last_login_at", DateTime, nullable=True
    )
    failedAttempts: Mapped[int] = mapped_column(
        "failed_attempts", Integer, nullable=False, default=0, comment="连续失败次数"
    )
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, server_default=func.current_timestamp()
    )
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at",
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        Index("uk_admin_users_username", "username", unique=True),
        Index("idx_admin_users_status", "status"),
    )


__all__ = ["AdminUser"]
