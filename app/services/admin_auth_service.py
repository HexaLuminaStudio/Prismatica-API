"""管理员鉴权服务(2026-08-06 重构):

- loginByPassword(db, username, password, ip) → AdminUser(成功) / ApiError(失败)
- changePassword(db, userId, newPassword) → None
- 写 audit_logs 由 admin_audit_service.recordAudit 代理
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import getSettings
from app.errors import ApiError
from app.models import AdminUser
from app.security.password import hashPassword, verifyPassword
from app.services.admin_audit_service import recordAudit

_settings = getSettings()


def loginByPassword(
    db: Session,
    username: str,
    password: str,
    ip: str | None = None,
) -> AdminUser:
    """用户名密码登录(成功 → 返回 AdminUser,失败 → ApiError)。

    错误码:
        - 401 ADMIN_INVALID_CREDENTIALS
        - 423 ADMIN_ACCOUNT_LOCKED
    """
    if not username or not password:
        raise ApiError("ADMIN_INVALID_CREDENTIALS", httpStatus=401)

    user = db.execute(select(AdminUser).where(AdminUser.username == username)).scalar_one_or_none()

    if user is None:
        recordAudit("anonymous", "admin.login_failed", details={"username": username}, ip=ip)
        raise ApiError("ADMIN_INVALID_CREDENTIALS", httpStatus=401)

    if user.status == "locked":
        recordAudit(user.username, "admin.login_locked", details={"reason": "locked"}, ip=ip)
        raise ApiError("ADMIN_ACCOUNT_LOCKED", httpStatus=423)

    if not verifyPassword(password, user.passwordHash):
        user.failedAttempts = int(user.failedAttempts or 0) + 1
        if user.failedAttempts >= _settings.adminMaxFailedAttempts:
            user.status = "locked"
            db.commit()
            recordAudit(
                user.username,
                "admin.login_locked",
                details={"reason": "too_many_failures", "failedAttempts": user.failedAttempts},
                ip=ip,
            )
            raise ApiError("ADMIN_ACCOUNT_LOCKED", httpStatus=423)
        db.commit()
        recordAudit(
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
    recordAudit(user.username, "admin.login_success", ip=ip)
    return user


def changePassword(db: Session, userId: str, newPassword: str) -> None:
    """修改密码(供 /v1/admin/auth/change-password 调用)。"""
    user = db.get(AdminUser, userId)
    if user is None:
        raise ApiError("NOT_FOUND", "管理员账号不存在")
    user.passwordHash = hashPassword(newPassword)
    db.commit()
    recordAudit(user.username, "admin.change_password")


__all__ = ["loginByPassword", "changePassword"]
