"""Admin 后端单测(2026-08-05 M2 B4)。

覆盖:
    - /admin/health 无需鉴权
    - /admin/login 缺字段 → 400
    - cookie 工具:make + verify 双向 / 错误签名 / 空值
    - requireAdminCookie 在无 cookie 时 → 401
    - requireAdminCookie 在错误 cookie 时 → 401
    - /openapi.json 包含 admin/* 与 /v1/admin/* 路径
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
# /admin/health
# ---------------------------------------------------------------------------


def test_admin_health_no_auth_needed(client):
    resp = client.get("/admin/health")
    assert resp.status_code == 200
    assert resp.get_json()["scope"] == "admin"


# ---------------------------------------------------------------------------
# /admin/login 缺字段 → 400(BAD_REQUEST envelope,纯 Pydantic 校验不查 DB)
# ---------------------------------------------------------------------------


def test_admin_login_missing_password_returns_400(client):
    resp = client.post("/admin/login", json={"username": "root"})
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["error"]["code"] == "BAD_REQUEST"


def test_admin_login_missing_username_returns_400(client):
    resp = client.post("/admin/login", json={"password": "anything"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# cookie 工具单元测试(纯函数,无需 DB)
# ---------------------------------------------------------------------------


def test_make_and_verify_session_value_roundtrip():
    value = makeSessionValue(userId="adm_test", username="root")
    payload = verifySessionValue(value)
    assert payload is not None
    assert payload["userId"] == "adm_test"
    assert payload["username"] == "root"


def test_verify_session_value_bad_signature_returns_none():
    payload = verifySessionValue("abc.bad_signature")
    assert payload is None


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
    assert body["error"]["code"] == "ADMIN_LOGIN_REQUIRED"


def test_admin_users_with_bad_cookie_returns_401(client):
    resp = client.get(
        "/v1/admin/users",
        headers={"Cookie": f"{COOKIE_NAME}=invalid_cookie_value"},
    )
    assert resp.status_code == 401


def test_admin_metrics_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/metrics-summary")
    assert resp.status_code == 401


def test_admin_audit_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/audit")
    assert resp.status_code == 401


def test_admin_audit_summary_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/audit-summary")
    assert resp.status_code == 401


def test_admin_codes_lookup_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/codes/lookup?code=foo")
    assert resp.status_code == 401


def test_admin_users_revoke_no_cookie_returns_401(client):
    resp = client.post("/v1/admin/users/some-user-id/revoke-sessions")
    assert resp.status_code == 401


def test_admin_grant_still_requires_admin_token_or_cookie(client):
    """grant 仍走 requireAdminToken(curl 兼容),无 token → 403。"""
    resp = client.post(
        "/v1/admin/grant",
        json={"userId": "u1", "amount": 10, "note": "x"},
    )
    assert resp.status_code == 403


def test_admin_issue_codes_still_requires_admin_token(client):
    resp = client.post(
        "/v1/admin/issue-codes",
        json={"kind": "invite", "count": 1},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# /openapi.json 包含 admin/* 与 /v1/admin/* 路径
# ---------------------------------------------------------------------------


def test_openapi_includes_admin_routes(app):
    client = app.test_client()
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.get_json()
    paths = spec["paths"]
    # login/logout/me 在 /admin/* 蓝图下
    assert any(p.endswith("/admin/login") for p in paths), paths
    assert any(p.endswith("/admin/logout") for p in paths), paths
    assert any(p.endswith("/admin/me") for p in paths), paths
    # v1 admin 用户管理
    assert any(p.endswith("/v1/admin/users") for p in paths), paths
    assert any(p.endswith("/v1/admin/audit") for p in paths), paths
    assert any(p.endswith("/v1/admin/audit-summary") for p in paths), paths
    assert any(p.endswith("/v1/admin/metrics-summary") for p in paths), paths
    assert any(p.endswith("/v1/admin/codes/lookup") for p in paths), paths


# ---------------------------------------------------------------------------
# X-Admin-Token 直通(curl 兼容路径)
# ---------------------------------------------------------------------------


def test_admin_with_correct_token_passes_through_to_endpoint(app):
    """X-Admin-Token 头可以让 requireAdminCookie 走 fallback 路径。"""
    settings = getSettings()
    client = app.test_client()
    resp = client.get(
        "/v1/admin/metrics-summary",
        headers={"X-Admin-Token": settings.adminToken},
    )
    # 不连 DB → endpoint 会抛 DB 错误 → 500;但 status 不会是 401/403 即视为通过 requireAdminCookie
    assert resp.status_code in (200, 500)
    assert resp.status_code not in (401, 403)
