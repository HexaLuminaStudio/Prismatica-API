"""应用工厂 smoke 测试:不连 DB,只验证路由注册 + envelope。"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app():
    """构造 Flask app(不连 DB)。"""
    from app.config import getSettings
    from app.main import createApp

    settings = getSettings()
    # 关闭 autoInitSchema,避免测试期尝试连库
    settings.autoInitSchema = False
    return createApp()


def test_healthz_returns_200(app):
    client = app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code in (200, 503)
    data = resp.get_json()
    # 2026-08-06 统一 envelope:{code, data, requestId}
    assert data["code"] == "OK"
    payload = data["data"]
    assert "status" in payload
    assert "db" in payload


def test_openapi_includes_v1_routes(app):
    client = app.test_client()
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.get_json()
    assert spec["openapi"].startswith("3.")
    assert "/v1/auth/redeem" in spec["paths"]
    assert "/v1/billing/estimate" in spec["paths"]
    assert "/v1/account/me" in spec["paths"]


def test_404_envelope(app):
    client = app.test_client()
    resp = client.get("/v1/nonexistent")
    assert resp.status_code == 404
    data = resp.get_json()
    # 2026-08-06 统一 envelope:顶层 code/message,不再嵌套 error
    assert data["code"] == "NOT_FOUND"
    assert data["message"]


def test_invalid_code_envelope(app):
    client = app.test_client()
    # redeem 用一个明显非法的码 → 应返回 INVALID_CODE envelope
    resp = client.post(
        "/v1/auth/redeem",
        json={"code": "NOT-VALID", "deviceId": "00000000-0000-0000-0000-000000000000"},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["code"] == "INVALID_CODE"


def test_missing_auth_returns_401(app):
    client = app.test_client()
    resp = client.get("/v1/account/me")
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["code"] == "UNAUTHORIZED"
