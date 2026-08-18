"""后台用户状态变更必须让既有令牌立即失效。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import IdentityDevice, IdentityUser, StoredRefreshToken
from app.services import admin_user_service


@pytest.fixture()
def dbContext(monkeypatch) -> Iterator[dict]:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def getTestDb():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    monkeypatch.setattr(admin_user_service, "getDb", getTestDb)
    monkeypatch.setattr(admin_user_service, "recordAudit", lambda **_kwargs: None)
    yield {"factory": factory}
    engine.dispose()


def testPauseUserRevokesDevicesAndRefreshTokens(dbContext) -> None:
    factory = dbContext["factory"]
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory() as db:
        user = IdentityUser(
            email="pause@example.com",
            passwordHash="!",
            displayName="Pause",
            tier="pro",
            status="active",
        )
        db.add(user)
        db.flush()
        device = IdentityDevice(
            userId=user.id,
            deviceId="device-pause",
            deviceName="Device",
            platform="test",
            status="active",
            firstSeenAt=now,
            lastSeenAt=now,
        )
        db.add(device)
        db.flush()
        token = StoredRefreshToken(
            jti="refresh-pause",
            tokenHash="hash-pause",
            userId=user.id,
            deviceId=device.id,
            expiresAt=now + timedelta(days=1),
        )
        db.add(token)
        db.commit()
        userId = user.id
        deviceId = device.id
        tokenId = token.id

    admin_user_service.updateUser(str(userId), status="paused")

    with factory() as db:
        pausedUser = db.get(IdentityUser, userId)
        assert pausedUser.status == "paused"
        assert pausedUser.authVersion == 1
        assert db.get(IdentityDevice, deviceId).status == "revoked"
        assert db.get(StoredRefreshToken, tokenId).revokedAt is not None
