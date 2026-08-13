"""数据库资源账号设备授权、短期票据与清单测试。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import jwt
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import getSettings
from app.db import Base
from app.errors import ApiError
from app.models.identity import IdentityDevice
from app.models.identity import User as IdentityUser
from app.security.password import hashPassword
from app.security.resource_ticket import (
    RESOURCE_TICKET_AUDIENCE,
    RESOURCE_TICKET_TYPE,
    createResourceTicket,
    verifyResourceTicket,
)
from app.services.resource_service import (
    ProtectedResource,
    authorizeResourceAccess,
    buildResourceManifests,
)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def resourceSettings(monkeypatch):
    settings = getSettings()
    monkeypatch.setattr(
        settings,
        "resourceTicketSecret",
        "test-resource-secret-at-least-32-bytes-long",
    )
    monkeypatch.setattr(settings, "resourceTicketTtlSec", 180)
    monkeypatch.setattr(settings, "hskCorpusSourceUrl", "https://origin.test/a.db")
    monkeypatch.setattr(settings, "hskCorpusSha256", "a" * 64)
    monkeypatch.setattr(settings, "hskCorpusVersion", "2026.08.1")
    monkeypatch.setattr(
        settings,
        "hskLocalCorpusSourceUrl",
        "https://origin.test/b.db",
    )
    monkeypatch.setattr(settings, "hskLocalCorpusSha256", "b" * 64)
    monkeypatch.setattr(settings, "hskLocalCorpusVersion", "2026.08.1")


def _createActiveAccount(db: Session) -> tuple[IdentityUser, IdentityDevice]:
    now = datetime.now(UTC).replace(tzinfo=None)
    user = IdentityUser(
        email="resource@example.com",
        passwordHash=hashPassword("Password1Aa"),
        displayName="Resource User",
        tier="pro",
        status="active",
    )
    db.add(user)
    db.flush()
    device = IdentityDevice(
        userId=user.id,
        deviceId="device-resource",
        deviceName="Test Device",
        platform="pytest",
        status="active",
        firstSeenAt=now,
        lastSeenAt=now,
    )
    db.add(device)
    db.flush()
    return user, device


def testBuildResourceManifestsHidesOriginAndIssuesShortUrls(db: Session) -> None:
    user, device = _createActiveAccount(db)

    manifests = buildResourceManifests(
        db,
        user.id,
        device.deviceId,
        "https://api.test",
    )

    assert {manifest.resourceKey for manifest in manifests} == {
        "hskCorpus",
        "hskLocalCorpus",
    }
    assert all(
        manifest.downloadUrl.startswith("https://api.test/v1/resources/download/")
        for manifest in manifests
    )
    assert all("origin.test" not in manifest.downloadUrl for manifest in manifests)
    assert all("ticket=" in manifest.downloadUrl for manifest in manifests)


def testAuthorizeResourceAccessAllowsUserWithoutSubscription(db: Session) -> None:
    user, device = _createActiveAccount(db)

    authorizedUser = authorizeResourceAccess(db, user.id, device.deviceId)

    assert authorizedUser.id == user.id


def testAuthorizeResourceAccessRejectsRevokedDevice(db: Session) -> None:
    user, device = _createActiveAccount(db)
    device.status = "revoked"
    db.flush()

    with pytest.raises(ApiError) as captured:
        authorizeResourceAccess(db, user.id, device.deviceId)

    assert captured.value.code == "FORBIDDEN"
    assert captured.value.httpStatus == 403


def testResourceTicketRejectsTamperingAndResourceReuse() -> None:
    ticket = createResourceTicket(
        42,
        "device-42",
        "hskCorpus",
        "2026.08.1",
    )
    claims = verifyResourceTicket(ticket, "hskCorpus", "2026.08.1")
    assert claims.userId == 42
    assert claims.deviceId == "device-42"

    with pytest.raises(ApiError) as tampered:
        verifyResourceTicket(f"{ticket[:-1]}x", "hskCorpus", "2026.08.1")
    assert tampered.value.code == "RESOURCE_TICKET_INVALID"

    with pytest.raises(ApiError) as wrongResource:
        verifyResourceTicket(ticket, "hskLocalCorpus", "2026.08.1")
    assert wrongResource.value.code == "RESOURCE_TICKET_INVALID"


def testResourceTicketRejectsExpiredToken() -> None:
    settings = getSettings()
    now = int(datetime.now(UTC).timestamp())
    expiredTicket = jwt.encode(
        {
            "iss": settings.jwtIssuer,
            "aud": RESOURCE_TICKET_AUDIENCE,
            "sub": "42",
            "device_id": "device-42",
            "resource_key": "hskCorpus",
            "resource_version": "2026.08.1",
            "token_type": RESOURCE_TICKET_TYPE,
            "jti": "expired-ticket",
            "iat": now - 120,
            "exp": now - 60,
        },
        settings.resourceTicketSecret,
        algorithm="HS256",
    )

    with pytest.raises(ApiError) as captured:
        verifyResourceTicket(expiredTicket, "hskCorpus", "2026.08.1")

    assert captured.value.code == "RESOURCE_TICKET_EXPIRED"


def testDownloadGatewayStreamsWithoutExposingOrigin(monkeypatch) -> None:
    from app.main import createApp
    from app.routers import resources as resourceRouter

    settings = getSettings()
    monkeypatch.setattr(settings, "autoInitSchema", False)
    resource = ProtectedResource(
        key="hskCorpus",
        displayName="HSK 作文数据表",
        fileName="hsk_corpus.db",
        sourceUrl="https://origin.test/private/hsk_corpus.db",
        sha256="a" * 64,
        version="1",
    )

    class FakeUpstreamResponse:
        headers = {"Content-Length": "8"}
        isClosed = False

        def raise_for_status(self) -> None:
            return None

        def iter_content(self, chunk_size: int):
            assert chunk_size == 256 * 1024
            yield b"database"

        def close(self) -> None:
            self.isClosed = True

    upstreamResponse = FakeUpstreamResponse()

    @contextmanager
    def fakeGetDb():
        yield object()

    monkeypatch.setattr(resourceRouter, "getConfiguredResource", lambda _key: resource)
    monkeypatch.setattr(
        resourceRouter,
        "verifyResourceTicket",
        lambda *_args: SimpleNamespace(userId=42, deviceId="device-42"),
    )
    monkeypatch.setattr(resourceRouter, "getDb", fakeGetDb)
    monkeypatch.setattr(resourceRouter, "authorizeResourceAccess", lambda *_args: None)
    monkeypatch.setattr(
        resourceRouter.requests,
        "get",
        lambda url, **_kwargs: upstreamResponse if url == resource.sourceUrl else None,
    )

    response = createApp().test_client().get(
        "/v1/resources/download/hskCorpus?ticket=short-lived"
    )

    assert response.status_code == 200
    assert response.data == b"database"
    assert response.headers["Content-Disposition"] == 'attachment; filename="hsk_corpus.db"'
    assert "origin.test" not in response.headers.get("Location", "")
    assert upstreamResponse.isClosed is True
