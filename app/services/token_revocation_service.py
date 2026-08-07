"""JWT jti 持久化吊销。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.revoked_token import RevokedToken


def _toNaiveUtc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def revokeJti(
    db: Session,
    jti: str,
    userId: int,
    tokenType: str,
    expiresAt: datetime,
    reason: str = "logout",
) -> RevokedToken:
    """幂等写入吊销记录；事务提交由调用方负责。"""
    if tokenType not in {"access", "refresh"}:
        raise ValueError("tokenType 必须是 access 或 refresh")
    existing = db.get(RevokedToken, jti)
    if existing is not None:
        return existing
    revoked = RevokedToken(
        jti=jti,
        userId=userId,
        tokenType=tokenType,
        reason=reason,
        expiresAt=_toNaiveUtc(expiresAt),
    )
    db.add(revoked)
    db.flush()
    return revoked


revoke_jti = revokeJti

__all__ = ["revokeJti", "revoke_jti"]
