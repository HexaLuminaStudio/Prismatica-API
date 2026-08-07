"""端到端 smoke(2026-08-06 重构验证):原 12 步版本,严格遵守 P0-A 计划承诺
"smoke_admin_e2e.py 未修改(原 12 个测试仍然过)"。

覆盖 12 步:
    [1]  health
    [2]  login(用户名密码) → 拿 cookie
    [3]  /auth/me(cookie)
    [4]  错误密码 → 401
    [5]  metrics summary
    [6]  users list
    [7]  准备一个测试用户
    [8]  users 详情
    [9]  grant balance
    [10] PATCH tier
    [11] revoke-sessions
    [12] codes: issue 3 个 invite
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("AUTO_INIT_SCHEMA", "false")
os.environ.setdefault("ADMIN_BOOTSTRAP_PASSWORD", "Sm0ke-Test-Pass!")

from app.config import getSettings
from app.db import getDb
from app.main import createApp
from app.models import AdminUser
from app.security.password import hashPassword


def _ensureRoot():
    settings = getSettings()
    with getDb() as db:
        u = db.query(AdminUser).filter(AdminUser.username == "root").one_or_none()
        if u is None:
            db.add(
                AdminUser(
                    userId="adm_smoke_root",
                    username="root",
                    passwordHash=hashPassword(settings.adminBootstrapPassword),
                    role="admin",
                    status="active",
                    failedAttempts=0,
                )
            )
        else:
            u.passwordHash = hashPassword(settings.adminBootstrapPassword)
            u.status = "active"
            u.failedAttempts = 0


def _okEnvelop(body, expectHttp: int, expectCode: str = "OK"):
    assert isinstance(body, dict), body
    assert body.get("code") == expectCode, f"code={body.get('code')} want={expectCode} body={body}"
    assert "data" in body, body
    return body["data"]


def _errEnvelope(body, expectCode: str):
    assert isinstance(body, dict), body
    assert body.get("code") == expectCode, f"code={body.get('code')} want={expectCode} body={body}"
    assert body.get("message"), body
    return body


def main() -> int:
    _ensureRoot()
    app = createApp()
    client = app.test_client()

    # 1) health
    r = client.get("/v1/admin/health")
    assert r.status_code == 200, r.data
    _okEnvelop(r.get_json(), 200)
    print("[1] health OK")

    # 2) login
    settings = getSettings()
    r = client.post(
        "/v1/admin/auth/login",
        json={"username": "root", "password": settings.adminBootstrapPassword},
    )
    assert r.status_code == 200, r.data
    me = _okEnvelop(r.get_json(), 200)
    assert me["username"] == "root"
    # 拿 cookie 给后续请求
    cookies = r.headers.get("Set-Cookie", "")
    cookieHeader = cookies.split(";")[0] if cookies else ""
    assert cookieHeader, "no cookie set"
    print(f"[2] login OK userId={me['userId']}")

    # 3) /auth/me
    r = client.get("/v1/admin/auth/me", headers={"Cookie": cookieHeader})
    assert r.status_code == 200
    me2 = _okEnvelop(r.get_json(), 200)
    assert me2["userId"] == me["userId"]
    print(f"[3] me OK username={me2['username']}")

    # 4) 错误密码 → 401
    r = client.post(
        "/v1/admin/auth/login", json={"username": "root", "password": "wrong"}
    )
    assert r.status_code == 401
    _errEnvelope(r.get_json(), "ADMIN_INVALID_CREDENTIALS")
    print("[4] wrong password → 401 ADMIN_INVALID_CREDENTIALS OK")

    # 5) metrics summary
    r = client.get("/v1/admin/metrics/summary", headers={"Cookie": cookieHeader})
    assert r.status_code == 200
    metrics = _okEnvelop(r.get_json(), 200)
    for k in (
        "userCount",
        "sevenDayActive",
        "sevenDayGrantTotal",
        "billsPending",
        "billsSettledLast7Days",
        "billsRefundedLast7Days",
    ):
        assert k in metrics, metrics
    print(f"[5] metrics summary OK userCount={metrics['userCount']}")

    # 6) users list
    r = client.get("/v1/admin/users?limit=5", headers={"Cookie": cookieHeader})
    assert r.status_code == 200
    ulist = _okEnvelop(r.get_json(), 200)
    assert "items" in ulist and "nextCursor" in ulist
    print(f"[6] users list OK count={len(ulist['items'])}")

    # 7) 准备一个测试用户
    import uuid

    from app.models import UserAccount, UserBalance
    from app.security.password import hashPassword

    smokeEmail = f"smoke_{uuid.uuid4().hex[:8]}@example.com"
    with getDb() as db:
        user = UserAccount(
            email=smokeEmail,
            passwordHash=hashPassword("Smoke-Test-Pass-2026!"),
            displayName="smoke-user",
            tier="pro",
            status="active",
        )
        db.add(user)
        db.flush()
        testUserId = str(user.id)
        db.add(UserBalance(userId=user.id, balance=0))
    print(f"[7] seed test user {testUserId}")

    # 8) users 详情
    r = client.get(f"/v1/admin/users/{testUserId}", headers={"Cookie": cookieHeader})
    assert r.status_code == 200
    detail = _okEnvelop(r.get_json(), 200)
    assert detail["userId"] == testUserId
    assert detail["balance"] == 0
    print(f"[8] user detail OK balance={detail['balance']}")

    # 9) grant balance
    r = client.post(
        f"/v1/admin/users/{testUserId}/grant",
        json={"amount": 500, "note": "smoke-test"},
        headers={"Cookie": cookieHeader},
    )
    assert r.status_code == 200, r.data
    grant = _okEnvelop(r.get_json(), 200)
    assert grant["newBalance"] == 500, grant
    print(f"[9] grant balance OK newBalance={grant['newBalance']}")

    # 10) PATCH tier
    r = client.patch(
        f"/v1/admin/users/{testUserId}",
        json={"tier": "team"},
        headers={"Cookie": cookieHeader},
    )
    assert r.status_code == 200, r.data
    upd = _okEnvelop(r.get_json(), 200)
    assert upd["tier"] == "team"
    print(f"[10] patch tier OK tier={upd['tier']}")

    # 11) revoke-sessions
    r = client.post(
        f"/v1/admin/users/{testUserId}/revoke-sessions",
        json={"reason": "smoke"},
        headers={"Cookie": cookieHeader},
    )
    assert r.status_code == 200
    rv = _okEnvelop(r.get_json(), 200)
    print(f"[11] revoke-sessions OK revokedCount={rv['revokedCount']}")

    # 12) codes: issue 3 个 invite
    r = client.post(
        "/v1/admin/codes",
        json={
            "kind": "invite",
            "count": 3,
            "grantedBalance": 50,
            "grantedDays": 7,
            "tier": "pro",
            "expireDays": 7,
        },
        headers={"Cookie": cookieHeader},
    )
    assert r.status_code == 200, r.data
    issued = _okEnvelop(r.get_json(), 200)
    assert len(issued["items"]) == 3
    firstCode = issued["items"][0]["code"]
    print(f"[12] issue invite codes OK count=3 first={firstCode}")

    print("\n=== ALL 12 STEPS PASSED ===")
    return 0


def testAdminSmokeE2E() -> None:
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
