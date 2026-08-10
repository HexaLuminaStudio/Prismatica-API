"""官方语料账号 Token 代理测试。"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import SecretStr

from app.config import getSettings
from app.errors import ApiError
from app.services import official_corpus_token_service as tokenService


class FakeResponse:
    def __init__(self, payload: dict, statusCode: int = 200) -> None:
        self.text = json.dumps(payload)
        self.status_code = statusCode

    def raise_for_status(self) -> None:
        return None


@pytest.fixture(autouse=True)
def officialCredentials(monkeypatch):
    settings = getSettings()
    monkeypatch.setattr(settings, "officialHskUsername", "official-hsk@example.test")
    monkeypatch.setattr(settings, "officialHskPassword", SecretStr("hsk-password"))
    monkeypatch.setattr(settings, "officialGlobalUsername", "official-global-user")
    monkeypatch.setattr(settings, "officialGlobalPassword", SecretStr("global-password"))
    monkeypatch.setattr(settings, "officialTokenConnectTimeoutSec", 3)
    monkeypatch.setattr(settings, "officialTokenReadTimeoutSec", 9)


def testHskOfficialTokenUsesBackendCredentialsWithoutReturningThem(monkeypatch) -> None:
    captured = {}

    def fakePost(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        captured["timeout"] = kwargs["timeout"]
        return FakeResponse({"code": 0, "data": "hsk-issued-token"})

    monkeypatch.setattr(tokenService.requests, "post", fakePost)

    token = tokenService.requestOfficialCorpusToken("hsk")

    assert token == "hsk-issued-token"
    assert captured["url"].endswith("/api/v1/login/access-token")
    assert captured["json"] == {
        "username": "official-hsk@example.test",
        "password": "hsk-password",
    }
    assert captured["timeout"] == (3, 9)


def testGlobalOfficialTokenHashesPasswordBeforeUpstreamRequest(monkeypatch) -> None:
    captured = {}

    def fakePost(_url, **kwargs):
        captured["json"] = kwargs["json"]
        return FakeResponse({"stats": "1", "token": "global-issued-token"})

    monkeypatch.setattr(tokenService.requests, "post", fakePost)

    token = tokenService.requestOfficialCorpusToken("global")

    assert token == "global-issued-token"
    assert captured["json"]["UserID"] == "official-global-user"
    assert captured["json"]["Password"] == hashlib.md5(
        b"global-password"
    ).hexdigest()


def testMissingOfficialCredentialsReturnsServiceUnavailable(monkeypatch) -> None:
    settings = getSettings()
    monkeypatch.setattr(settings, "officialHskPassword", SecretStr(""))

    with pytest.raises(ApiError) as captured:
        tokenService.requestOfficialCorpusToken("hsk")

    assert captured.value.code == "OFFICIAL_ACCOUNT_UNAVAILABLE"
    assert captured.value.httpStatus == 503


def testOfficialTokenEndpointUsesEnvelopeAndValidatesProvider(monkeypatch) -> None:
    from app.main import createApp
    from app.routers import resources as resourceRouter

    settings = getSettings()
    monkeypatch.setattr(settings, "autoInitSchema", False)
    monkeypatch.setattr(
        resourceRouter,
        "requestOfficialCorpusToken",
        lambda provider: f"{provider}-token",
    )
    client = createApp().test_client()

    headers = {"X-Device-Id": "pytest-device-id"}
    response = client.post(
        "/v1/resources/official-token",
        json={"provider": "hsk"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.get_json()["data"] == {
        "provider": "hsk",
        "token": "hsk-token",
    }

    invalidResponse = client.post(
        "/v1/resources/official-token",
        json={"provider": "unknown"},
        headers=headers,
    )
    assert invalidResponse.status_code == 400
    assert invalidResponse.get_json()["code"] == "BAD_REQUEST"

    missingDeviceResponse = client.post(
        "/v1/resources/official-token",
        json={"provider": "hsk"},
    )
    assert missingDeviceResponse.status_code == 400
    assert missingDeviceResponse.get_json()["code"] == "BAD_REQUEST"
