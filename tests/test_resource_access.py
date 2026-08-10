"""受保护资源订阅授权、短期票据与清单测试。"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import getSettings
from app.db import Base
from app.errors import ApiError
from app.models.identity import IdentityDevice
from app.models.identity import User as IdentityUser
from app.models.subscription import Subscription
from app.schemas.resources import DeviceResourceKeyRequest
from app.security.password import hashPassword
from app.security.resource_crypto import (
    buildDeviceKeyProofMessage,
    canonicalJson,
    generateResourceDataKey,
)
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
    buildSignedResourceBootstrap,
    registerDeviceResourceKey,
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
    monkeypatch.setattr(settings, "resourceKmsProvider", "local")
    monkeypatch.setattr(settings, "resourceKmsLocalKey", base64.b64encode(os.urandom(32)).decode("ascii"))
    monkeypatch.setattr(settings, "resourceManifestSignerProvider", "local")
    signingKey = ed25519.Ed25519PrivateKey.generate()
    signingKeyDer = signingKey.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setattr(
        settings,
        "resourceManifestSigningPrivateKey",
        base64.b64encode(signingKeyDer).decode("ascii"),
    )
    monkeypatch.setattr(settings, "resourceManifestSigningKeyId", "test-signing-key")
    monkeypatch.setattr(settings, "hskCorpusSourceUrl", "https://origin.test/a.db")
    monkeypatch.setattr(settings, "hskCorpusSha256", "a" * 64)
    monkeypatch.setattr(settings, "hskCorpusVersion", "2026.08.1")
    _, hskWrappedKey = generateResourceDataKey("hskCorpus", "2026.08.1", settings)
    monkeypatch.setattr(settings, "hskCorpusWrappedKey", hskWrappedKey)
    monkeypatch.setattr(
        settings,
        "hskLocalCorpusSourceUrl",
        "https://origin.test/b.db",
    )
    monkeypatch.setattr(settings, "hskLocalCorpusSha256", "b" * 64)
    monkeypatch.setattr(settings, "hskLocalCorpusVersion", "2026.08.1")
    _, localWrappedKey = generateResourceDataKey("hskLocalCorpus", "2026.08.1", settings)
    monkeypatch.setattr(settings, "hskLocalCorpusWrappedKey", localWrappedKey)
    return signingKey.public_key()


def _createAuthorizedAccount(db: Session) -> tuple[IdentityUser, IdentityDevice]:
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
        resourceEncryptionPublicKey=base64.b64encode(
            x25519.X25519PrivateKey.generate().public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        ).decode("ascii"),
        firstSeenAt=now,
        lastSeenAt=now,
    )
    db.add(device)
    db.add(
        Subscription(
            userId=user.id,
            planCode="pro",
            status="active",
            startedAt=now,
            currentPeriodStart=now,
            currentPeriodEnd=now + timedelta(days=30),
            expiresAt=now + timedelta(days=30),
            nextGrantAt=None,
            autoRenew=False,
            monthlyQuota=1000,
        )
    )
    db.flush()
    return user, device


def testBuildResourceManifestsHidesOriginAndIssuesShortUrls(db: Session) -> None:
    user, device = _createAuthorizedAccount(db)

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
    assert all(manifest.sqlCipherCompatibility == 4 for manifest in manifests)
    assert all(manifest.wrappedDatabaseKey.ciphertext for manifest in manifests)


def testSignedBootstrapCoversDeviceAndEncryptedResources(
    db: Session,
    resourceSettings,
) -> None:
    user, device = _createAuthorizedAccount(db)

    response = buildSignedResourceBootstrap(
        db,
        user.id,
        device.deviceId,
        "https://api.test",
    )

    unsignedPayload = {
        "manifestVersion": response.manifestVersion,
        "issuedAt": response.issuedAt,
        "expiresAt": response.expiresAt,
        "deviceId": response.deviceId,
        "resources": [item.model_dump(mode="json") for item in response.resources],
    }
    resourceSettings.verify(
        base64.b64decode(response.signature),
        canonicalJson(unsignedPayload),
    )
    assert response.signingKeyId == "test-signing-key"
    assert response.deviceId == device.deviceId


def testDeviceResourceKeyRegistrationRequiresProofAndRejectsReplacement(
    db: Session,
) -> None:
    user, device = _createAuthorizedAccount(db)
    device.resourceEncryptionPublicKey = None
    device.resourceSigningPublicKey = None
    db.flush()
    encryptionPrivateKey = x25519.X25519PrivateKey.generate()
    signingPrivateKey = ed25519.Ed25519PrivateKey.generate()
    encryptionPublicKey = base64.b64encode(
        encryptionPrivateKey.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    signingPublicKey = base64.b64encode(
        signingPrivateKey.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    proof = base64.b64encode(
        signingPrivateKey.sign(
            buildDeviceKeyProofMessage(
                device.deviceId,
                encryptionPublicKey,
                signingPublicKey,
            )
        )
    ).decode("ascii")
    payload = DeviceResourceKeyRequest(
        encryptionPublicKey=encryptionPublicKey,
        signingPublicKey=signingPublicKey,
        proof=proof,
    )

    first = registerDeviceResourceKey(db, user.id, device.deviceId, payload)
    second = registerDeviceResourceKey(db, user.id, device.deviceId, payload)

    assert first.registered is True
    assert second.registered is False
    conflictingEncryptionKey = base64.b64encode(os.urandom(32)).decode("ascii")
    conflicting = payload.model_copy(
        update={
            "encryptionPublicKey": conflictingEncryptionKey,
            "proof": base64.b64encode(
                signingPrivateKey.sign(
                    buildDeviceKeyProofMessage(
                        device.deviceId,
                        conflictingEncryptionKey,
                        signingPublicKey,
                    )
                )
            ).decode("ascii"),
        }
    )
    with pytest.raises(ApiError) as captured:
        registerDeviceResourceKey(db, user.id, device.deviceId, conflicting)
    assert captured.value.code == "RESOURCE_DEVICE_KEY_CONFLICT"


def testAuthorizeResourceAccessRequiresActiveSubscription(db: Session) -> None:
    user, device = _createAuthorizedAccount(db)
    subscription = authorizeResourceAccess(db, user.id, device.deviceId)
    subscription.status = "expired"
    db.flush()

    with pytest.raises(ApiError) as captured:
        authorizeResourceAccess(db, user.id, device.deviceId)

    assert captured.value.code == "RESOURCE_SUBSCRIPTION_REQUIRED"
    assert captured.value.httpStatus == 403


def testAuthorizeResourceAccessRejectsRevokedDevice(db: Session) -> None:
    user, device = _createAuthorizedAccount(db)
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

    header, payload, signature = ticket.split(".")
    replacement = "A" if payload[10] != "A" else "B"
    tamperedTicket = f"{header}.{payload[:10]}{replacement}{payload[11:]}.{signature}"
    with pytest.raises(ApiError) as tampered:
        verifyResourceTicket(tamperedTicket, "hskCorpus", "2026.08.1")
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
        wrappedKey=base64.b64encode(b"x" * 32).decode("ascii"),
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
