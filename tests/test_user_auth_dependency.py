from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from flask import Flask, g, jsonify

from app import deps
from app.errors import ApiError, registerErrorHandlers
from app.models import RevokedToken
from app.security.jwt import create_access_token
from app.services.token_revocation_service import revoke_jti


class FakeDb:
    def __init__(self, revokedJtis: set[str] | None = None) -> None:
        self.revokedJtis = revokedJtis or set()
        self.added: list[object] = []

    def get(self, model, key):
        if model is RevokedToken and key in self.revokedJtis:
            return RevokedToken(
                jti=key,
                userId=42,
                tokenType="access",
                reason="logout",
                expiresAt=datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5),
            )
        return None

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        return None


def testAuthenticateUserTokenAcceptsMatchingDevice() -> None:
    token = create_access_token(42, "device-42", "pro", "jti-ok")

    context = deps.authenticateUserToken(token, "device-42", FakeDb())

    assert context.userId == 42
    assert context.deviceId == "device-42"
    assert context.tier == "pro"
    assert context.jti == "jti-ok"


def testAuthenticateUserTokenRejectsDeviceMismatch() -> None:
    token = create_access_token(42, "device-42", "free", "jti-mismatch")

    with pytest.raises(ApiError) as captured:
        deps.authenticateUserToken(token, "another-device", FakeDb())

    assert captured.value.httpStatus == 401


def testAuthenticateUserTokenRejectsRevokedJti() -> None:
    token = create_access_token(42, "device-42", "free", "jti-revoked")

    with pytest.raises(ApiError) as captured:
        deps.authenticateUserToken(token, "device-42", FakeDb({"jti-revoked"}))

    assert captured.value.code == "TOKEN_REVOKED"
    assert captured.value.httpStatus == 401


def testRequireUserPublishesAuthenticatedContext(monkeypatch) -> None:
    fakeDb = FakeDb()

    @contextmanager
    def fakeGetDb():
        yield fakeDb

    monkeypatch.setattr(deps, "getDb", fakeGetDb)
    app = Flask(__name__)
    registerErrorHandlers(app)

    @app.get("/protected")
    @deps.require_user
    def protected():
        return jsonify(
            userId=g.userId,
            deviceId=g.deviceId,
            tier=g.tier,
            jti=g.jti,
        )

    token = create_access_token(42, "device-42", "pro", "jti-route")
    response = app.test_client().get(
        "/protected",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Device-Id": "device-42",
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "deviceId": "device-42",
        "jti": "jti-route",
        "tier": "pro",
        "userId": 42,
    }


def testRevokeJtiIsIdempotent() -> None:
    db = FakeDb()
    expiresAt = datetime.now(UTC) + timedelta(minutes=15)

    first = revoke_jti(db, "jti-new", 42, "access", expiresAt)
    db.revokedJtis.add("jti-new")
    second = revoke_jti(db, "jti-new", 42, "access", expiresAt)

    assert first.jti == "jti-new"
    assert second.jti == "jti-new"
    assert len(db.added) == 1
