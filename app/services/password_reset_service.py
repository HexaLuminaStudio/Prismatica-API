"""找回密码、确认重置和登录态修改密码。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models.identity import PasswordResetToken, User
from app.security.password import hashPassword, verifyPassword
from app.services.email_stub import sendPasswordResetEmail
from app.services.identity_auth_service import normalizeEmail
from app.services.token_service import revokeAllRefreshTokens

RESET_TOKEN_TTL_MINUTES = 30


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def hashResetToken(rawToken: str) -> str:
    return hashlib.sha256(rawToken.encode("utf-8")).hexdigest()


def _hashNewPassword(newPassword: str, currentHash: str | None = None) -> str:
    if currentHash is not None and verifyPassword(newPassword, currentHash):
        raise ApiError("BAD_REQUEST", "新密码不能与当前密码相同", httpStatus=400)
    try:
        return hashPassword(newPassword)
    except ValueError as error:
        raise ApiError("BAD_REQUEST", str(error), httpStatus=400) from error


def requestPasswordReset(db: Session, email: str) -> str | None:
    """为存在的 active 用户签发 token；未知邮箱返回 None，调用方仍统一返回 200。"""
    user = db.execute(select(User).where(User.email == normalizeEmail(email)).with_for_update()).scalar_one_or_none()
    if user is None or user.status != "active" or user.deletedAt is not None:
        return None

    now = _now()
    outstanding = (
        db.execute(
            select(PasswordResetToken)
            .where(
                PasswordResetToken.userId == user.id,
                PasswordResetToken.usedAt.is_(None),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    for token in outstanding:
        token.usedAt = now

    rawToken = secrets.token_urlsafe(32)
    expiresAt = now + timedelta(minutes=RESET_TOKEN_TTL_MINUTES)
    db.add(
        PasswordResetToken(
            userId=user.id,
            tokenHash=hashResetToken(rawToken),
            expiresAt=expiresAt,
        )
    )
    db.flush()
    sendPasswordResetEmail(user.email, rawToken, expiresAt)
    return rawToken


def confirmPasswordReset(db: Session, rawToken: str, newPassword: str) -> int:
    token = db.execute(
        select(PasswordResetToken).where(PasswordResetToken.tokenHash == hashResetToken(rawToken)).with_for_update()
    ).scalar_one_or_none()
    if token is None:
        raise ApiError("RESET_TOKEN_INVALID", httpStatus=400)
    if token.usedAt is not None:
        raise ApiError("RESET_TOKEN_USED", httpStatus=410)
    if token.expiresAt <= _now():
        raise ApiError("RESET_TOKEN_EXPIRED", httpStatus=410)

    user = db.get(User, token.userId)
    if user is None or user.status != "active" or user.deletedAt is not None:
        raise ApiError("RESET_TOKEN_INVALID", httpStatus=400)
    user.passwordHash = _hashNewPassword(newPassword, user.passwordHash)
    user.failedLoginCount = 0
    user.lockedUntil = None
    token.usedAt = _now()
    revokedCount = revokeAllRefreshTokens(db, user.id, "password_reset")
    db.flush()
    return revokedCount


def changePassword(
    db: Session,
    userId: int,
    oldPassword: str,
    newPassword: str,
) -> int:
    user = db.execute(select(User).where(User.id == userId).with_for_update()).scalar_one_or_none()
    if user is None or user.status != "active" or user.deletedAt is not None:
        raise ApiError("INVALID_CREDENTIALS", httpStatus=401)
    if not verifyPassword(oldPassword, user.passwordHash):
        raise ApiError("INVALID_CREDENTIALS", "当前密码错误", httpStatus=401)
    user.passwordHash = _hashNewPassword(newPassword, user.passwordHash)
    user.failedLoginCount = 0
    user.lockedUntil = None
    revokedCount = revokeAllRefreshTokens(db, user.id, "password_change")
    db.flush()
    return revokedCount


hash_reset_token = hashResetToken
request_password_reset = requestPasswordReset
confirm_password_reset = confirmPasswordReset
change_password = changePassword

__all__ = [
    "RESET_TOKEN_TTL_MINUTES",
    "hashResetToken",
    "requestPasswordReset",
    "confirmPasswordReset",
    "changePassword",
    "hash_reset_token",
    "request_password_reset",
    "confirm_password_reset",
    "change_password",
]
