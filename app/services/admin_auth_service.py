"""管理员鉴权服务(2026-08-05 M2 B1)

提供:
    - loginByPassword:bcrypt 校验 + 失败计数 + 锁定(连续失败 >= max → 锁)
    - 写 audit_logs(成功/失败两条记录)

不动 session cookie(set/clear 由 router 层用 admin_session 工具)
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import getSettings
from app.db import getDb
from app.errors import ApiError
from app.models import AdminUser, AuditLog
from app.security.password import hashPassword, verifyPassword


_settings = getSettings()


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _audit(
    actor: str,
    action: str,
    targetUser: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> None:
    """写审计日志(独立事务,失败不影响主流程)。"""
    try:
        with getDb() as db:
            db.add(
                AuditLog(
                    actor=actor,
                    action=action,
                    targetUser=targetUser,
                    details=details,
                    ip=ip,
                )
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[AdminAuth] audit 失败: {e}")


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def loginByPassword(
    db: Session,
    username: str,
    password: str,
    ip: str | None = None,
) -> AdminUser:
    """用户名密码登录(成功 → 返回 AdminUser,失败 → ApiError)。

    错误码:
        - 401 ADMIN_INVALID_CREDENTIALS:用户名或密码错
        - 423 ADMIN_ACCOUNT_LOCKED:连续失败次数超阈值,账号被锁
    """
    if not username or not password:
        raise ApiError("ADMIN_INVALID_CREDENTIALS", httpStatus=401)

    user = db.execute(
        select(AdminUser).where(AdminUser.username == username)
    ).scalar_one_or_none()

    if user is None:
        _audit("anonymous", "admin.login_failed", details={"username": username}, ip=ip)
        raise ApiError("ADMIN_INVALID_CREDENTIALS", httpStatus=401)

    if user.status == "locked":
        _audit(user.username, "admin.login_locked", details={"reason": "locked"}, ip=ip)
        raise ApiError("ADMIN_ACCOUNT_LOCKED", httpStatus=423)

    if not verifyPassword(password, user.passwordHash):
        user.failedAttempts = int(user.failedAttempts or 0) + 1
        if user.failedAttempts >= _settings.adminMaxFailedAttempts:
            user.status = "locked"
            db.commit()
            _audit(
                user.username,
                "admin.login_locked",
                details={"reason": "too_many_failures", "failedAttempts": user.failedAttempts},
                ip=ip,
            )
            raise ApiError("ADMIN_ACCOUNT_LOCKED", httpStatus=423)
        db.commit()
        _audit(
            user.username,
            "admin.login_failed",
            details={"failedAttempts": user.failedAttempts},
            ip=ip,
        )
        raise ApiError("ADMIN_INVALID_CREDENTIALS", httpStatus=401)

    # 成功
    user.failedAttempts = 0
    user.lastLoginAt = datetime.now(UTC).replace(tzinfo=None)
    db.commit()
    _audit(user.username, "admin.login_success", ip=ip)
    return user


def changePassword(
    db: Session,
    userId: str,
    newPassword: str,
) -> None:
    """修改密码(供 /admin/me/change-password 路由调用)。"""
    user = db.get(AdminUser, userId)
    if user is None:
        raise ApiError("NOT_FOUND", "管理员账号不存在")
    user.passwordHash = hashPassword(newPassword)
    db.commit()
    _audit(user.username, "admin.change_password")


__all__ = ["loginByPassword", "changePassword"]
