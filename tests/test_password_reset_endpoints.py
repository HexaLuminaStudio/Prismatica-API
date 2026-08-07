from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import deps
from app.config import getSettings
from app.db import Base
from app.main import createApp
from app.routers import account as accountRouter
from app.routers import auth as authRouter
from app.services import password_reset_service as passwordService


@pytest.fixture()
def client(monkeypatch) -> Iterator:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def sessionContext() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(authRouter, "_sessionCtx", sessionContext)
    monkeypatch.setattr(accountRouter, "_sessionCtx", sessionContext)
    monkeypatch.setattr(deps, "getDb", sessionContext)
    getSettings().autoInitSchema = False
    app = createApp()
    app.config["TESTING"] = True
    yield app.test_client()
    engine.dispose()


def testResetAndChangePasswordEndpointFlow(client, monkeypatch) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(passwordService, "sendPasswordResetEmail", lambda *args: sent.append(args))
    assert (
        client.post(
            "/v1/auth/register",
            json={"email": "password-flow@example.com", "password": "Prismatica2026!"},
        ).status_code
        == 201
    )
    login = client.post(
        "/v1/auth/login",
        json={
            "email": "password-flow@example.com",
            "password": "Prismatica2026!",
            "deviceId": "password-device",
        },
    ).get_json()["data"]

    unknownResponse = client.post(
        "/v1/auth/password/reset-request",
        json={"email": "unknown-password@example.com"},
    )
    knownResponse = client.post(
        "/v1/auth/password/reset-request",
        json={"email": "password-flow@example.com"},
    )
    assert unknownResponse.status_code == knownResponse.status_code == 200
    assert unknownResponse.get_json()["data"] == knownResponse.get_json()["data"]
    rawToken = sent[0][1]

    confirmResponse = client.post(
        "/v1/auth/password/reset-confirm",
        json={"token": rawToken, "newPassword": "ResetPrismatica2027!"},
    )
    assert confirmResponse.status_code == 200
    assert confirmResponse.get_json()["data"]["revokedRefreshTokens"] == 1

    reusedResponse = client.post(
        "/v1/auth/password/reset-confirm",
        json={"token": rawToken, "newPassword": "AnotherPrismatica2028!"},
    )
    assert reusedResponse.status_code == 410
    assert reusedResponse.get_json()["code"] == "RESET_TOKEN_USED"

    oldRefresh = client.post(
        "/v1/auth/refresh",
        json={"refreshToken": login["tokens"]["refreshToken"]},
        headers={"X-Device-Id": "password-device"},
    )
    assert oldRefresh.status_code == 401
    assert oldRefresh.get_json()["code"] == "TOKEN_REVOKED"

    relogin = client.post(
        "/v1/auth/login",
        json={
            "email": "password-flow@example.com",
            "password": "ResetPrismatica2027!",
            "deviceId": "password-device",
        },
    ).get_json()["data"]
    changeResponse = client.post(
        "/v1/auth/password/change",
        json={
            "oldPassword": "ResetPrismatica2027!",
            "newPassword": "ChangedPrismatica2028!",
        },
        headers={
            "Authorization": f"Bearer {relogin['tokens']['accessToken']}",
            "X-Device-Id": "password-device",
        },
    )
    assert changeResponse.status_code == 200
    assert changeResponse.get_json()["data"]["revokedRefreshTokens"] == 1

    changedRefresh = client.post(
        "/v1/auth/refresh",
        json={"refreshToken": relogin["tokens"]["refreshToken"]},
        headers={"X-Device-Id": "password-device"},
    )
    assert changedRefresh.status_code == 401
    assert changedRefresh.get_json()["code"] == "TOKEN_REVOKED"
