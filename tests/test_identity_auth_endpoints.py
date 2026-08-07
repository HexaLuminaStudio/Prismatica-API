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


@pytest.fixture()
def client(monkeypatch) -> Iterator:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 2026-08-07:IdentityBase / TokenBase 已经是 Base 的别名,所有 P0-A 表
    # 共享同一 metadata,一次 create_all 即可。
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


def testRegisterLoginMeRefreshLogoutFlow(client) -> None:
    registerResponse = client.post(
        "/v1/auth/register",
        json={
            "email": "Flow@Example.com",
            "password": "Prismatica2026!",
            "displayName": "Flow User",
        },
    )
    assert registerResponse.status_code == 201
    registered = registerResponse.get_json()["data"]["user"]
    assert registered["email"] == "flow@example.com"
    assert registered["tier"] == "free"

    duplicateResponse = client.post(
        "/v1/auth/register",
        json={"email": "FLOW@example.com", "password": "Prismatica2026!"},
    )
    assert duplicateResponse.status_code == 409
    assert duplicateResponse.get_json()["code"] == "EMAIL_ALREADY_USED"

    loginResponse = client.post(
        "/v1/auth/login",
        json={
            "email": "flow@example.com",
            "password": "Prismatica2026!",
            "deviceId": "flow-device",
            "deviceName": "Test Desktop",
            "platform": "windows",
        },
    )
    assert loginResponse.status_code == 200
    tokens = loginResponse.get_json()["data"]["tokens"]

    meResponse = client.get(
        "/v1/account/me",
        headers={
            "Authorization": f"Bearer {tokens['accessToken']}",
            "X-Device-Id": "flow-device",
        },
    )
    assert meResponse.status_code == 200
    me = meResponse.get_json()["data"]
    assert me["email"] == "flow@example.com"
    assert me["available"] == 0

    refreshResponse = client.post(
        "/v1/auth/refresh",
        json={"refreshToken": tokens["refreshToken"]},
        headers={"X-Device-Id": "flow-device"},
    )
    assert refreshResponse.status_code == 200
    rotatedTokens = refreshResponse.get_json()["data"]["tokens"]
    assert rotatedTokens["refreshToken"] != tokens["refreshToken"]

    reusedResponse = client.post(
        "/v1/auth/refresh",
        json={"refreshToken": tokens["refreshToken"]},
        headers={"X-Device-Id": "flow-device"},
    )
    assert reusedResponse.status_code == 401
    assert reusedResponse.get_json()["code"] == "TOKEN_REVOKED"

    logoutResponse = client.post(
        "/v1/auth/logout",
        json={"refreshToken": rotatedTokens["refreshToken"]},
    )
    assert logoutResponse.status_code == 204

    loggedOutRefresh = client.post(
        "/v1/auth/refresh",
        json={"refreshToken": rotatedTokens["refreshToken"]},
        headers={"X-Device-Id": "flow-device"},
    )
    assert loggedOutRefresh.status_code == 401
    assert loggedOutRefresh.get_json()["code"] == "TOKEN_REVOKED"


def testLoginEmailRateLimitReturnsEnvelope(client) -> None:
    registerResponse = client.post(
        "/v1/auth/register",
        json={"email": "rate-limit@example.com", "password": "Prismatica2026!"},
    )
    assert registerResponse.status_code == 201

    responses = [
        client.post(
            "/v1/auth/login",
            json={
                "email": "rate-limit@example.com",
                "password": "WrongPassword1!",
                "deviceId": "rate-device",
            },
        )
        for _ in range(6)
    ]

    assert responses[4].status_code == 423
    assert responses[4].get_json()["code"] == "ACCOUNT_LOCKED"
    assert responses[5].status_code == 429
    assert responses[5].get_json()["code"] == "RATE_LIMITED"
