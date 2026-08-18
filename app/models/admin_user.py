"""管理员账号(2026-08-05 M2 B1 新增;2026-08-06 M3 扩展 owner/admin + 软删 + 密码重置标记)

用于 PrismaticaAdmin 管理后台登录。bcrypt(stretch=12)哈希密码。
role:    owner / admin(owner 可管理 admin_users 资源;admin 仅可访问自身相关接口)
status:  active / locked(被管理员锁定 / 超阈值登录失败)
deleted_at: 软删时间戳,username 一旦软删永久占用,不释放。
pwd_reset_at: 重置密码时间戳,让旧 cookie 在下一次请求时被作废。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AdminUser(Base):
    """管理员账号表。"""

    __tablename__ = "admin_users"

    userId: Mapped[str] = mapped_column("user_id", String(36), primary_key=True, comment="管理员 UUID")
    username: Mapped[str] = mapped_column(
        "username", String(64), nullable=False, comment="登录用户名(唯一,软删后永久占用)"
    )
    passwordHash: Mapped[str] = mapped_column(
        "password_hash", String(255), nullable=False, comment="bcrypt(stretch=12) 哈希"
    )
    role: Mapped[str] = mapped_column(
        "role",
        String(32),
        nullable=False,
        default="admin",
        comment="角色:owner / admin",
    )
    status: Mapped[str] = mapped_column(
        "status",
        String(16),
        nullable=False,
        default="active",
        comment="active / locked",
    )
    lastLoginAt: Mapped[datetime | None] = mapped_column("last_login_at", DateTime, nullable=True)
    failedAttempts: Mapped[int] = mapped_column(
        "failed_attempts", Integer, nullable=False, default=0, comment="连续失败次数"
    )
    deletedAt: Mapped[datetime | None] = mapped_column(
        "deleted_at",
        DateTime,
        nullable=True,
        comment="软删除时间戳;非空即已删除,username 永久占用",
    )
    pwdResetAt: Mapped[datetime | None] = mapped_column(
        "pwd_reset_at",
        DateTime,
        nullable=True,
        comment="密码重置时间戳;cookie 颁发时间早于此值即失效",
    )
    createdAt: Mapped[datetime] = mapped_column("created_at", DateTime, server_default=func.current_timestamp())
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at",
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )

    __table_args__ = (
        Index("uk_admin_users_username", "username", unique=True),
        Index("idx_admin_users_status", "status"),
        Index("idx_admin_users_deleted_at", "deleted_at"),
    )


__all__ = ["AdminUser"]
