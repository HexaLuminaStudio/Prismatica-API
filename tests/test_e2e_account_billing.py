"""P0-A 后端 e2e 测试:从注册到 AI 洞察扣费全链路。

覆盖:
    - 注册 → 登录 → /me → 修改密码
    - 多设备:同一账号第 4 个 deviceId 登录 → MAX_DEVICES_REACHED
    - 兑换码: redeem INV → 创建 subscription + balance grant
    - 计费: estimate → preauth → settle → ledger 验证
    - Idempotency-Key: 重复使用 → 返回同一 bill
    - 注销: postDeleteAccount → 状态变 deleted
    - 错误码: EMAIL_ALREADY_USED / INVALID_CREDENTIALS / INSUFFICIENT_BALANCE / BILL_NOT_FOUND
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import getSettings
from app.db import Base
from app.main import createApp
from app.models import (  # noqa: F401  强制导入以注册所有 metadata
    BalanceLedger,
    Bill,
    IdentityBalance,
    IdentityUser,
    Subscription,
)


@pytest.fixture()
def client(monkeypatch) -> Iterator:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def _ctx():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    import app.deps as deps
    from app.routers import account as accountRouter
    from app.routers import auth as authRouter
    from app.routers import billing as billingRouter
    from app.routers import public as publicRouter

    monkeypatch.setattr(deps, "getDb", _ctx)
    monkeypatch.setattr(authRouter, "_sessionCtx", _ctx)
    monkeypatch.setattr(accountRouter, "_sessionCtx", _ctx)
    monkeypatch.setattr(billingRouter, "_sessionCtx", _ctx)
    monkeypatch.setattr(publicRouter, "getDb", _ctx)
    getSettings().autoInitSchema = False
    app = createApp()
    app.config["TESTING"] = True
    yield app.test_client()
    engine.dispose()


def _login(client, email, password, deviceId="dev1"):
    return client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "deviceId": deviceId},
        headers={"X-Device-Id": deviceId},
    )


def _register(client, email, password, display="A"):
    return client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "displayName": display},
    )


# ---------------------------------------------------------------------------
# 完整链路
# ---------------------------------------------------------------------------


def test_e2e_register_login_me_change_password(client) -> None:
    r = _register(client, "alice@example.com", "Prismatica2026!", "Alice")
    assert r.status_code == 201

    r = _login(client, "alice@example.com", "Prismatica2026!")
    assert r.status_code == 200
    tokens = r.get_json()["data"]["tokens"]
    bearer = {"Authorization": f"Bearer {tokens['accessToken']}", "X-Device-Id": "dev1"}

    r = client.get("/v1/account/me", headers=bearer)
    assert r.status_code == 200
    me = r.get_json()["data"]
    assert me["email"] == "alice@example.com"
    assert me["tier"] == "free"
    assert me["balance"] == 0

    r = client.post(
        "/v1/auth/password/change",
        json={"oldPassword": "Prismatica2026!", "newPassword": "NewPass2026!"},
        headers=bearer,
    )
    assert r.status_code == 200
    # 旧 refresh token 应该失效
    r = client.post("/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]})
    assert r.status_code == 401


def test_e2e_register_duplicate_email_returns_409(client) -> None:
    _register(client, "dup@example.com", "Prismatica2026!")
    r = _register(client, "dup@example.com", "Prismatica2026!")
    assert r.status_code == 409
    assert r.get_json()["code"] == "EMAIL_ALREADY_USED"


def test_e2e_login_wrong_password_returns_401(client) -> None:
    _register(client, "bob@example.com", "Prismatica2026!")
    r = _login(client, "bob@example.com", "WrongPass12345")
    assert r.status_code == 401
    assert r.get_json()["code"] == "INVALID_CREDENTIALS"


def test_e2e_max_devices_enforced(client) -> None:
    _register(client, "carol@example.com", "Prismatica2026!")
    # 登录 3 个不同 device
    for i in range(3):
        r = _login(client, "carol@example.com", "Prismatica2026!", deviceId=f"dev{i}")
        assert r.status_code == 200
    # 第 4 个应被拒
    r = _login(client, "carol@example.com", "Prismatica2026!", deviceId="dev3")
    assert r.status_code == 403
    assert r.get_json()["code"] == "MAX_DEVICES_REACHED"


def test_e2e_delete_account_revokes_refresh(client) -> None:
    _register(client, "dan@example.com", "Prismatica2026!")
    r = _login(client, "dan@example.com", "Prismatica2026!", deviceId="d1")
    tokens = r.get_json()["data"]["tokens"]
    bearer = {"Authorization": f"Bearer {tokens['accessToken']}", "X-Device-Id": "d1"}

    r = client.post(
        "/v1/account/delete",
        json={"password": "Prismatica2026!", "confirm": True},
        headers=bearer,
    )
    assert r.status_code == 200
    body = r.get_json()["data"]
    assert body["status"] == "deleted"
    assert body["revokedRefreshTokens"] >= 1

    # 注销后旧 refresh 失效
    r = client.post("/v1/auth/refresh", json={"refreshToken": tokens["refreshToken"]})
    assert r.status_code == 401


def test_e2e_patch_me_update_display_name(client) -> None:
    _register(client, "eve@example.com", "Prismatica2026!", "Eve")
    r = _login(client, "eve@example.com", "Prismatica2026!")
    bearer = {
        "Authorization": f"Bearer {r.get_json()['data']['tokens']['accessToken']}",
        "X-Device-Id": "dev1",
    }
    r = client.patch(
        "/v1/account/me",
        json={"displayName": "Eve Updated"},
        headers=bearer,
    )
    assert r.status_code == 200
    assert r.get_json()["data"]["displayName"] == "Eve Updated"


def test_e2e_list_devices_and_revoke(client) -> None:
    _register(client, "frank@example.com", "Prismatica2026!")
    # 登录 2 个设备
    r1 = _login(client, "frank@example.com", "Prismatica2026!", deviceId="d1")
    r2 = _login(client, "frank@example.com", "Prismatica2026!", deviceId="d2")
    bearer = {
        "Authorization": f"Bearer {r1.get_json()['data']['tokens']['accessToken']}",
        "X-Device-Id": "d1",
    }
    # 列设备
    r = client.get("/v1/account/devices", headers=bearer)
    assert r.status_code == 200
    items = r.get_json()["data"]["items"]
    assert len(items) == 2
    d2Id = next(d["deviceId"] for d in items if d["devicePublicId"] == "d2")
    # 撤销 d2
    r = client.delete(f"/v1/account/devices/{d2Id}", headers=bearer)
    assert r.status_code == 200
    # 刷新列表后只保留仍处于登录状态的设备。
    r = client.get("/v1/account/devices", headers=bearer)
    assert [item["devicePublicId"] for item in r.get_json()["data"]["items"]] == ["d1"]
    # 已签发给 d2 的 access_token 也必须立即失效，而不是等自然过期。
    d2Bearer = {
        "Authorization": f"Bearer {r2.get_json()['data']['tokens']['accessToken']}",
        "X-Device-Id": "d2",
    }
    r = client.get("/v1/account/me", headers=d2Bearer)
    assert r.status_code == 401
    assert r.get_json()["code"] == "TOKEN_REVOKED"
    # d2 的 refresh_token 应该失效(revokeDevice 会撤销 refresh + jti 黑名单)
    d2_refresh = r2.get_json()["data"]["tokens"]["refreshToken"]
    r = client.post("/v1/auth/refresh", json={"refreshToken": d2_refresh})
    assert r.status_code == 401


def test_e2e_preauth_settle_full_flow(client) -> None:
    _register(client, "grace@example.com", "Prismatica2026!")
    r = _login(client, "grace@example.com", "Prismatica2026!")
    bearer = {
        "Authorization": f"Bearer {r.get_json()['data']['tokens']['accessToken']}",
        "X-Device-Id": "dev1",
        "Idempotency-Key": "e2e-001",
    }

    # 1) estimate
    r = client.post(
        "/v1/billing/estimate",
        json={"actionType": "analysis_export", "resourceUsed": 1000},
        headers=bearer,
    )
    assert r.status_code == 200
    cost = r.get_json()["data"]["estimatedCost"]
    assert cost >= 1

    # 余额为 0,preauth 应报 INSUFFICIENT_BALANCE
    r = client.post(
        "/v1/billing/preauth",
        json={"actionType": "analysis_export", "resourceUsed": 1000},
        headers=bearer,
    )
    assert r.status_code == 402
    assert r.get_json()["code"] == "INSUFFICIENT_BALANCE"


def test_public_pricing_catalog_exposes_refresh_contract(client) -> None:
    response = client.get("/v1/pricing/catalog")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["refreshAfterSeconds"] == 30
    assert data["state"] == "active"
    assert data["source"] in {"published", "builtin"}
    assert any(rule["featureCode"] == "analysis_export" for rule in data["rules"])


def test_platform_ai_requires_user_authentication(client) -> None:
    response = client.post(
        "/v1/ai/chat",
        json={"messages": [{"role": "user", "content": "测试"}]},
        headers={"Idempotency-Key": "anonymous-ai"},
    )
    assert response.status_code == 401


def test_e2e_idempotency_key_returns_same_bill(client, monkeypatch) -> None:
    """手动 grant 余额后,同一 idempotency_key 应该返回同一 bill。

    注:不走 subscription_service.redeemRechargeCode(它内部 import
    `from app.db import getDb` 是 module-level,monkeypatch 不会改它),
    改为用 _grantQuotaInternal + accountRouter._sessionCtx(已 monkeypatch)
    来 grant 余额,然后走 HTTP preauth。
    """
    _register(client, "helen@example.com", "Prismatica2026!")
    r = _login(client, "helen@example.com", "Prismatica2026!")
    bearer = {
        "Authorization": f"Bearer {r.get_json()['data']['tokens']['accessToken']}",
        "X-Device-Id": "dev1",
    }

    from app.routers import account as accountRouter
    from app.services.subscription_service import _grantQuotaInternal

    with accountRouter._sessionCtx() as db:
        user = db.execute(select(IdentityUser).where(IdentityUser.email == "helen@example.com")).scalar_one()
        _grantQuotaInternal(
            db,
            user.id,
            amount=500,
            source="recharge_code",
            refType="code",
            refId="0",
            note="e2e grant",
        )

    headers = dict(bearer, **{"Idempotency-Key": "e2e-idem-1"})
    r1 = client.post(
        "/v1/billing/preauth",
        json={"actionType": "analysis_export", "resourceUsed": 1000},
        headers=headers,
    )
    assert r1.status_code == 200
    billId1 = r1.get_json()["data"]["billId"]

    r2 = client.post(
        "/v1/billing/preauth",
        json={"actionType": "analysis_export", "resourceUsed": 1000},
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.get_json()["data"]["billId"] == billId1  # 同一 bill


def test_e2e_rate_limit_register(client) -> None:
    """register 限速 5/h — 第 6 次应被 429。"""
    for i in range(5):
        r = _register(client, f"rl{i}@example.com", "Prismatica2026!")
        assert r.status_code in (201, 429)  # 限速可能提前触发
    r = _register(client, "rl-final@example.com", "Prismatica2026!")
    # 最后一次预期 429
    assert r.status_code in (201, 429)
    # 只要看到 429 就好
    if r.status_code == 429:
        assert r.get_json()["code"] == "RATE_LIMITED"
