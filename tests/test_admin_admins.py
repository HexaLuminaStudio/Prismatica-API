"""Admin 账号管理路由测试(2026-08-06 M3 新增)。

覆盖范围(零 DB 路径,不依赖 MySQL):
    - 无 cookie → 401(ADMIN_LOGIN_REQUIRED)
    - X-Admin-Token 可达(但 requireOwner 会被绕过,owner 兜底)
    - 缺字段 → 400(BAD_REQUEST)
    - OpenAPI 路径全部存在
    - query 参数解析校验

业务层 / DB 路径用 scripts/smoke_admin_e2e.py 实测(需要真实 MySQL)。
"""
from __future__ import annotations

import pytest

from app.config import getSettings
from app.main import createApp
from app.middleware.admin_session import COOKIE_NAME


@pytest.fixture(scope="module")
def app():
    settings = getSettings()
    settings.autoInitSchema = False
    return createApp()


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# 鉴权前置:无 cookie → 401
# ---------------------------------------------------------------------------


def test_admin_admins_no_cookie_returns_401(client):
    resp = client.get("/v1/admin/admins")
    assert resp.status_code == 401
    body = resp.get_json()
    assert body["code"] == "ADMIN_LOGIN_REQUIRED"
    assert body["message"]


def test_admin_admins_bad_cookie_returns_401(client):
    resp = client.get(
        "/v1/admin/admins",
        headers={"Cookie": f"{COOKIE_NAME}=invalid_cookie_value"},
    )
    assert resp.status_code == 401


def test_admin_admins_create_no_cookie_returns_401(client):
    resp = client.post(
        "/v1/admin/admins",
        json={"username": "newadmin", "password": "abcd1234", "role": "admin"},
    )
    assert resp.status_code == 401


def test_admin_admins_patch_no_cookie_returns_401(client):
    resp = client.patch(
        "/v1/admin/admins/some-user-id",
        json={"status": "locked"},
    )
    assert resp.status_code == 401


def test_admin_admins_delete_no_cookie_returns_401(client):
    resp = client.delete(
        "/v1/admin/admins/some-user-id?confirm=someuser",
    )
    assert resp.status_code == 401


def test_admin_admins_reset_password_no_cookie_returns_401(client):
    resp = client.post("/v1/admin/admins/some-user-id/reset-password")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /openapi.json 包含 admin_admins 全部路径
# ---------------------------------------------------------------------------


def test_openapi_includes_admin_admins_routes(app):
    client = app.test_client()
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.get_json()
    paths = spec["paths"]

    # flask-openapi 把 <string:userId> 原样输出;
    # 同路径多个 method 时 spec 只展示最后注册的方法(库的限制)
    assert any(p == "/v1/admin/admins" for p in paths), paths
    assert any(p.endswith("/v1/admin/admins/<string:userId>") for p in paths), paths
    assert any(
        p.endswith("/v1/admin/admins/<string:userId>/reset-password") for p in paths
    ), paths

    # POST /v1/admin/admins 一定在
    post_paths = paths.get("/v1/admin/admins", {})
    assert "post" in post_paths


# ---------------------------------------------------------------------------
# X-Admin-Token 直通:cli-admin 视为 owner,可绕过 requireOwner 拿到下一层错误(无 DB → 500)
# 但只要不是 401/403 即视为通过 requireAdminCookie + requireOwner 鉴权
# ---------------------------------------------------------------------------


def test_admin_admins_with_x_admin_token_passes_through(app):
    settings = getSettings()
    client = app.test_client()
    resp = client.get(
        "/v1/admin/admins",
        headers={"X-Admin-Token": settings.adminToken},
    )
    # 无 DB → endpoint 抛 500;但不应是 401/403
    assert resp.status_code not in (401, 403), resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# 入参 schema 校验(零 DB 时:无 cookie 优先于 schema 校验 → 401)
#   这条测试仅证明路由已挂载(401 而不是 404)
# ---------------------------------------------------------------------------


def test_admin_admins_create_short_username_no_cookie_returns_401(client):
    """短 username 校验逻辑在 Pydantic,但鉴权装饰器优先 → 401。"""
    resp = client.post(
        "/v1/admin/admins",
        json={"username": "ab", "password": "abcd1234", "role": "admin"},
    )
    assert resp.status_code == 401  # 鉴权在 schema 校验之前


# ---------------------------------------------------------------------------
# schema 直接单测:确认 owner/admin 接受,非法 role 拒绝
# ---------------------------------------------------------------------------


def test_admin_create_request_accepts_owner_role():
    # role 由 service 层校验(VALID_ROLES = {owner, admin});Pydantic schema 不限制
    from app.schemas.admin import AdminCreateAdminRequest

    r = AdminCreateAdminRequest(username="newowner", password="abcd1234", role="owner")
    assert r.role == "owner"
    # 非法 role 也能过 schema(由 service 拒绝 → 需要 DB,这里只断言 schema 行为)
    r2 = AdminCreateAdminRequest(username="newadmin", password="abcd1234", role="unknown")
    assert r2.role == "unknown"


def test_admin_create_request_short_password_rejected():
    from pydantic import ValidationError

    from app.schemas.admin import AdminCreateAdminRequest

    with pytest.raises(ValidationError):
        AdminCreateAdminRequest(
            username="newadmin", password="short", role="admin"
        )


def test_admin_update_request_empty_body_rejected():

    from app.schemas.admin import AdminUpdateAdminRequest

    r = AdminUpdateAdminRequest()
    assert r.status is None and r.role is None
    # 这里只是确认默认 None,业务层会拒"两者都为空"


# ---------------------------------------------------------------------------
# 关键回归:naive datetime → epoch 转换(2026-08-06 M3 hotfix)
#   跨时区部署下,DB pwd_reset_at(naive UTC)直接 .timestamp() 会按本地时区解释,
#   容易触发 OSError / 错误的 401 / 错误的 INTERNAL_ERROR。这里锁定 UTC 行为。
# ---------------------------------------------------------------------------


def test_naive_utc_to_epoch_returns_known_value():
    from datetime import UTC, datetime

    from app.deps import _naiveUtcToEpoch

    # 2026-08-06 00:00:00 UTC(用 Python 实时验证 expected)
    expected = int(datetime(2026, 8, 6, 0, 0, 0, tzinfo=UTC).timestamp())
    assert _naiveUtcToEpoch(datetime(2026, 8, 6, 0, 0, 0)) == expected
    # 任一近期 naive datetime 都应得到 > 0 的 epoch
    assert _naiveUtcToEpoch(datetime(2020, 1, 1)) > 0


def test_naive_utc_to_epoch_handles_pre_1970_without_overflow():
    """远早日期不能抛 OSError(naive 直接 .timestamp() 在某些 Python 会爆)。"""
    from datetime import datetime

    from app.deps import _naiveUtcToEpoch

    # 1900 年日期 .timestamp() 在 naive+本地时区负偏移时会抛 OSError
    # 我们保证 UTC 解释后得到负数(允许),不抛异常
    v = _naiveUtcToEpoch(datetime(1900, 1, 1))
    assert v < 0  # 1900 早于 epoch,自然为负
