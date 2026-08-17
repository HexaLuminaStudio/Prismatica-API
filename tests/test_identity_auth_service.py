from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.errors import ApiError
from app.models.identity import User
from app.security.jwt import decodeAccessToken
from app.services.identity_auth_service import (
    login_user,
    logout,
    refresh_tokens,
    register_user,
)


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


def testRegisterAndLoginHappyPath(db: Session) -> None:
    user = register_user(db, "User@Example.com", "Prismatica2026!", "Prismatica")
    db.commit()

    result = login_user(
        db,
        "user@example.com",
        "Prismatica2026!",
        "device-1",
        "Desktop",
        "windows",
    )
    db.commit()

    assert user.email == "user@example.com"
    assert result.user.id == user.id
    assert result.tokens.accessToken
    assert result.tokens.refreshToken
    assert decodeAccessToken(result.tokens.accessToken)["auth_version"] == 0


def testDuplicateEmailIsRejected(db: Session) -> None:
    register_user(db, "duplicate@example.com", "Prismatica2026!")
    db.commit()

    with pytest.raises(ApiError) as captured:
        register_user(db, "DUPLICATE@example.com", "Prismatica2026!")

    assert captured.value.code == "EMAIL_ALREADY_USED"
    assert captured.value.httpStatus == 409


def testUnknownEmailUsesGenericInvalidCredentials(db: Session) -> None:
    with pytest.raises(ApiError) as captured:
        login_user(db, "missing@example.com", "WrongPassword1!", "missing-device")

    assert captured.value.code == "INVALID_CREDENTIALS"
    assert captured.value.httpStatus == 401


def testFiveFailedLoginsLockAccount(db: Session) -> None:
    register_user(db, "locked@example.com", "Prismatica2026!")
    db.commit()

    for _ in range(4):
        with pytest.raises(ApiError) as captured:
            login_user(db, "locked@example.com", "WrongPassword1!", "device-lock")
        assert captured.value.code == "INVALID_CREDENTIALS"

    with pytest.raises(ApiError) as captured:
        login_user(db, "locked@example.com", "WrongPassword1!", "device-lock")

    assert captured.value.code == "ACCOUNT_LOCKED"
    user = db.execute(select(User).where(User.email == "locked@example.com")).scalar_one()
    assert user.failedLoginCount == 5
    assert user.lockedUntil is not None


def testFourthActiveDeviceIsRejected(db: Session) -> None:
    register_user(db, "devices@example.com", "Prismatica2026!")
    db.commit()
    for index in range(3):
        login_user(
            db,
            "devices@example.com",
            "Prismatica2026!",
            f"device-{index}",
        )
        db.commit()

    with pytest.raises(ApiError) as captured:
        login_user(
            db,
            "devices@example.com",
            "Prismatica2026!",
            "device-4",
        )

    # 2026-08-07 P0-A M9 错误码语义化:语义归类到 TOO_MANY_DEVICES(细节 category),
    # envelope 仍保留 MAX_DEVICES_REACHED 以兼容桌面端 cloud_api 拦截。
    assert captured.value.code == "MAX_DEVICES_REACHED"
    assert captured.value.details == {"limit": 3, "category": "TOO_MANY_DEVICES"}


def testRefreshRotatesAndOldTokenIsImmediatelyRevoked(db: Session) -> None:
    register_user(db, "refresh@example.com", "Prismatica2026!")
    db.commit()
    login = login_user(
        db,
        "refresh@example.com",
        "Prismatica2026!",
        "refresh-device",
    )
    db.commit()
    oldRefresh = login.tokens.refreshToken

    rotated = refresh_tokens(db, oldRefresh, "refresh-device")
    db.commit()

    assert rotated.tokens.refreshToken != oldRefresh
    with pytest.raises(ApiError) as captured:
        refresh_tokens(db, oldRefresh, "refresh-device")
    assert captured.value.code == "TOKEN_REVOKED"


def testRefreshRejectsDifferentDevice(db: Session) -> None:
    register_user(db, "refresh-device@example.com", "Prismatica2026!")
    db.commit()
    login = login_user(
        db,
        "refresh-device@example.com",
        "Prismatica2026!",
        "bound-device",
    )
    db.commit()

    with pytest.raises(ApiError) as captured:
        refresh_tokens(db, login.tokens.refreshToken, "other-device")

    assert captured.value.code == "REFRESH_INVALID"
    assert captured.value.httpStatus == 401


def testRefreshRejectsOldAuthenticationVersion(db: Session) -> None:
    user = register_user(db, "refresh-version@example.com", "Prismatica2026!")
    db.commit()
    login = login_user(
        db,
        "refresh-version@example.com",
        "Prismatica2026!",
        "version-device",
    )
    db.commit()
    user.authVersion = 1
    db.commit()

    with pytest.raises(ApiError) as captured:
        refresh_tokens(db, login.tokens.refreshToken, "version-device")

    assert captured.value.code == "TOKEN_REVOKED"


def testLogoutIsIdempotentAndRevokesRefresh(db: Session) -> None:
    register_user(db, "logout@example.com", "Prismatica2026!")
    db.commit()
    login = login_user(
        db,
        "logout@example.com",
        "Prismatica2026!",
        "logout-device",
    )
    db.commit()

    assert logout(db, login.tokens.refreshToken) is True
    db.commit()
    assert logout(db, login.tokens.refreshToken) is False

    with pytest.raises(ApiError) as captured:
        refresh_tokens(db, login.tokens.refreshToken, "logout-device")
    assert captured.value.code == "TOKEN_REVOKED"
