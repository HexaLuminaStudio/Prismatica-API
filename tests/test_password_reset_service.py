from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.errors import ApiError
from app.models.identity import PasswordResetToken
from app.security.password import verify_password
from app.services import password_reset_service as passwordService
from app.services.identity_auth_service import login_user, refresh_tokens, register_user


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session
    engine.dispose()


def testResetRequestDoesNotRevealUnknownEmail(db: Session, monkeypatch) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(passwordService, "sendPasswordResetEmail", lambda *args: sent.append(args))

    rawToken = passwordService.request_password_reset(db, "unknown@example.com")

    assert rawToken is None
    assert sent == []
    assert db.execute(select(PasswordResetToken)).scalars().all() == []


def testResetTokenIsHashedSingleUseAndRevokesAllRefreshTokens(db: Session, monkeypatch) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(passwordService, "sendPasswordResetEmail", lambda *args: sent.append(args))
    user = register_user(db, "reset@example.com", "Prismatica2026!")
    db.commit()
    firstLogin = login_user(db, user.email, "Prismatica2026!", "reset-device-1")
    db.commit()
    secondLogin = login_user(db, user.email, "Prismatica2026!", "reset-device-2")
    db.commit()
    user.failedLoginCount = 5
    user.lockedUntil = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=15)
    db.commit()

    rawToken = passwordService.request_password_reset(db, user.email)
    db.commit()
    stored = db.execute(select(PasswordResetToken)).scalar_one()

    assert rawToken is not None
    assert sent[0][1] == rawToken
    assert stored.tokenHash == passwordService.hash_reset_token(rawToken)
    assert rawToken not in stored.tokenHash

    revokedCount = passwordService.confirm_password_reset(db, rawToken, "NewPrismatica2027!")
    db.commit()

    assert revokedCount == 2
    db.refresh(user)
    assert verify_password("NewPrismatica2027!", user.passwordHash)
    assert user.failedLoginCount == 0
    assert user.lockedUntil is None
    for refreshToken, deviceId in (
        (firstLogin.tokens.refreshToken, "reset-device-1"),
        (secondLogin.tokens.refreshToken, "reset-device-2"),
    ):
        with pytest.raises(ApiError) as captured:
            refresh_tokens(db, refreshToken, deviceId)
        assert captured.value.code == "TOKEN_REVOKED"

    with pytest.raises(ApiError) as captured:
        passwordService.confirm_password_reset(db, rawToken, "AnotherPassword2028!")
    assert captured.value.code == "RESET_TOKEN_USED"
    assert captured.value.httpStatus == 410


def testExpiredResetTokenReturns410(db: Session) -> None:
    user = register_user(db, "expired-reset@example.com", "Prismatica2026!")
    db.commit()
    rawToken = "expired-reset-token-value-123456"
    db.add(
        PasswordResetToken(
            userId=user.id,
            tokenHash=passwordService.hash_reset_token(rawToken),
            expiresAt=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1),
        )
    )
    db.commit()

    with pytest.raises(ApiError) as captured:
        passwordService.confirm_password_reset(db, rawToken, "NewPrismatica2027!")

    assert captured.value.code == "RESET_TOKEN_EXPIRED"
    assert captured.value.httpStatus == 410


def testChangePasswordVerifiesOldPasswordAndRevokesRefresh(db: Session) -> None:
    user = register_user(db, "change@example.com", "Prismatica2026!")
    db.commit()
    login = login_user(db, user.email, "Prismatica2026!", "change-device")
    db.commit()

    with pytest.raises(ApiError) as captured:
        passwordService.change_password(
            db,
            user.id,
            "WrongPassword1!",
            "ChangedPrismatica2027!",
        )
    assert captured.value.code == "INVALID_CREDENTIALS"

    revokedCount = passwordService.change_password(
        db,
        user.id,
        "Prismatica2026!",
        "ChangedPrismatica2027!",
    )
    db.commit()

    assert revokedCount == 1
    with pytest.raises(ApiError) as captured:
        refresh_tokens(db, login.tokens.refreshToken, "change-device")
    assert captured.value.code == "TOKEN_REVOKED"
