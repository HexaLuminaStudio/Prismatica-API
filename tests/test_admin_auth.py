"""Admin 后端单测(2026-08-06 重构:统一 envelope + 新路径)。

覆盖:
    - /v1/admin/health 无需鉴权
    - /v1/admin/auth/login 缺字段 → 400(BAD_REQUEST envelope)
    - cookie 工具:make + verify 双向 / 错误签名 / 空值
    - requireAdminCookie 在无 cookie 时 → 401(ADMIN_LOGIN_REQUIRED)
    - requireAdminCookie 在错误 cookie 时 → 401
    - /openapi.json 包含新 /v1/admin/* 路径
    - X-Admin-Token 直通(curl/脚本兼容)
    - 新统一 envelope:2xx 顶层有 code=OK,4xx 顶层有 code/message
"""

from __future__ import annotations

import pytest

from app.config import getSettings
from app.main import createApp
from app.middleware.admin_session import (
    COOKIE_NAME,
    makeSessionValue,
    verifySessionValue,
)


@pytest.fixture(scope="module")
def app():
    settings = getSettings()
    settings.autoInitSchema = False
    return createApp()


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# /v1/admin/health
# ---------------------------------------------------------------------------


def test_admin_health_no_auth_needed(client):
    resp = client.get("/v1/admin/health")
    assert resp.status_code in (200, 503)
    body = resp.get_json()
    assert body["code"] == "OK"
    payload = body["data"]
    assert payload["service"] == "prismatica-backend"
    assert payload["version"] == "2026.08.07-p0b"
    assert payload["build"] == "local"
    assert payload["commit"] == "unknown"
    assert payload["db"] in ("up", "down")


# ---------------------------------------------------------------------------
# /v1/admin/auth/login 缺字段 → 400(BAD_REQUEST envelope,顶层 code/message)
# ---------------------------------------------------------------------------


def test_admin_login_missing_password_returns_400(client):
    resp = client.post("/v1/admin/auth/login", json={"username": "root"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "BAD_REQUEST"
    assert body["message"]
    assert body.get("requestId")  # 由 request_id 中间件注入


def test_admin_login_missing_username_returns_400(client):
    resp = client.post("/v1/admin/auth/login", json={"password": "anything"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["code"] == "BAD_REQUEST"


# ---------------------------------------------------------------------------
# cookie 工具单元测试
# ---------------------------------------------------------------------------


def test_make_and_verify_session_value_roundtrip():
    value = makeSessionValue(userId="adm_test", username="root")
    payload = verifySessionValue(value)
    assert payload is not None
    assert payload["userId"] == "adm_test"
    assert payload["username"] == "root"


def test_verify_session_value_bad_signature_returns_none():
    assert verifySessionValue("abc.bad_signature") is None


def test_verify_session_value_invalid_format_returns_none():
    assert verifySessionValue("no_dot_here") is None
    assert verifySessionValue("") is None
    assert verifySessionValue(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# requireAdminCookie 装饰器(无 DB 也能跑:不查 user 表直接 401)
# ---------------------------------------------------------------------------


def test_admin_users_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/users")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["code"] == "ADMIN_LOGIN_REQUIRED"
    assert body["message"]


def test_admin_users_with_bad_cookie_returns_401(client):
    resp = client.get(
        "/v1/admin/users",
        headers={"Cookie": f"{COOKIE_NAME}=invalid_cookie_value"},
    )
    assert resp.status_code == 401


def test_admin_metrics_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/metrics/summary")
    assert resp.status_code == 401
    assert resp.get_json()["code"] == "ADMIN_LOGIN_REQUIRED"


def test_admin_audit_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/audit")
    assert resp.status_code == 401


def test_admin_audit_summary_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/audit/summary")
    assert resp.status_code == 401


def test_admin_codes_lookup_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/codes/lookup?code=foo")
    assert resp.status_code == 401


def test_admin_users_revoke_no_cookie_returns_401(client):
    resp = client.post("/v1/admin/users/some-user-id/revoke-sessions")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /openapi.json 包含新 /v1/admin/* 路径
# ---------------------------------------------------------------------------


def test_openapi_includes_admin_routes(app):
    client = app.test_client()
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.get_json()
    paths = spec["paths"]
    # auth
    assert any(p.endswith("/v1/admin/auth/login") for p in paths), paths
    assert any(p.endswith("/v1/admin/auth/logout") for p in paths), paths
    assert any(p.endswith("/v1/admin/auth/me") for p in paths), paths
    # users
    assert any(p.endswith("/v1/admin/users") for p in paths), paths
    # codes
    assert any(p.endswith("/v1/admin/codes") for p in paths), paths
    assert any(p.endswith("/v1/admin/codes/lookup") for p in paths), paths
    # audit
    assert any(p.endswith("/v1/admin/audit") for p in paths), paths
    assert any(p.endswith("/v1/admin/audit/summary") for p in paths), paths
    # metrics
    assert any(p.endswith("/v1/admin/metrics/summary") for p in paths), paths


# ---------------------------------------------------------------------------
# X-Admin-Token 直通(curl 兼容路径) - 注意:旧 grant / issue-codes 端点已下线
# ---------------------------------------------------------------------------


def test_admin_with_correct_token_passes_through_to_endpoint(app):
    """X-Admin-Token 头可以让 requireAdminCookie 走 fallback 路径。"""
    settings = getSettings()
    client = app.test_client()
    resp = client.get(
        "/v1/admin/metrics/summary",
        headers={"X-Admin-Token": settings.adminToken},
    )
    # 不连 DB → endpoint 会抛 DB 错误 → 500;但 status 不会是 401/403 即视为通过 requireAdminCookie
    assert resp.status_code in (200, 500)
    assert resp.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# 成功响应必须包 envelope(code=OK + data)
# ---------------------------------------------------------------------------


def test_health_response_envelope(client):
    resp = client.get("/v1/admin/health")
    body = resp.get_json()
    assert body["code"] == "OK"
    assert "data" in body
    assert "requestId" in body


# ---------------------------------------------------------------------------
# 404/405 走统一 envelope
# ---------------------------------------------------------------------------


def test_404_envelope(client):
    resp = client.get("/v1/admin/this-does-not-exist")
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["code"] == "NOT_FOUND"
    assert body["message"]
