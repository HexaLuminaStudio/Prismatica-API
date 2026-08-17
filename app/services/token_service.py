"""Access/Refresh Token 的持久化编排。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.stored_refresh_token import StoredRefreshToken
from app.security.jwt import createRefreshToken, decodeRefreshToken
from app.services.token_revocation_service import revokeJti


def hashToken(rawToken: str) -> str:
    """数据库只保存 token SHA-256，不保存可直接复用的原文。"""
    return hashlib.sha256(rawToken.encode("utf-8")).hexdigest()


def issueRefreshToken(
    db: Session,
    userId: int,
    deviceRecordId: int,
    devicePublicId: str,
    jti: str | None = None,
    authVersion: int = 0,
) -> tuple[str, StoredRefreshToken]:
    """签发 Refresh Token 并加入当前 DB 事务。"""
    rawToken = createRefreshToken(
        userId,
        devicePublicId,
        jti,
        authVersion=authVersion,
    )
    claims = decodeRefreshToken(rawToken)
    record = StoredRefreshToken(
        jti=str(claims["jti"]),
        tokenHash=hashToken(rawToken),
        userId=userId,
        deviceId=deviceRecordId,
        expiresAt=datetime.fromtimestamp(int(claims["exp"]), tz=UTC).replace(tzinfo=None),
    )
    db.add(record)
    db.flush()
    return rawToken, record


def revokeAllRefreshTokens(
    db: Session,
    userId: int,
    reason: str,
) -> int:
    """撤销用户当前全部有效 Refresh Token，并同步写 jti 黑名单。"""
    records = (
        db.execute(
            select(StoredRefreshToken)
            .where(
                StoredRefreshToken.userId == userId,
                StoredRefreshToken.revokedAt.is_(None),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    for record in records:
        record.revokedAt = now
        record.revokeReason = reason
        revokeJti(
            db,
            record.jti,
            record.userId,
            "refresh",
            record.expiresAt,
            reason=reason,
        )
    db.flush()
    return len(records)


hash_token = hashToken
issue_refresh_token = issueRefreshToken
revoke_all_refresh_tokens = revokeAllRefreshTokens

__all__ = [
    "hashToken",
    "issueRefreshToken",
    "revokeAllRefreshTokens",
    "hash_token",
    "issue_refresh_token",
    "revoke_all_refresh_tokens",
]
