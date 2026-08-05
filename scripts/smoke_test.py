"""端到端烟测:调用 FastAPI(此处 Flask)对外 HTTP 接口走完 redeem → me →
preauth → settle → bills 全流程。

执行:
    uv run python scripts/smoke_test.py
"""
from __future__ import annotations

import base64
import json
import sys
import uuid
from datetime import datetime, timedelta

import requests

BASE = "http://127.0.0.1:8000"
DEVICE_ID = str(uuid.uuid4())


def _hr(title: str) -> None:
    print(f"\n===== {title} =====")


def _ok(label: str, resp: requests.Response) -> dict:
    print(f"[{resp.status_code}] {label}")
    print(json.dumps(resp.json(), ensure_ascii=False, indent=2)[:600])
    print()
    resp.raise_for_status()
    return resp.json()


def makeInvite() -> str:
    """本地用同一 LICENSE_SECRET 签发一份邀请码(与客户端 signed_code 同款)。"""
    from app.config import getSettings
    from app.models.license_models import InviteCode, UserTier
    from app.security import hmac as hmacUtil

    getSettings()
    payload = InviteCode(
        code="INV-AAAA-BBBB-CCCC-DDDD",
        grantedBalance=100,
        grantedDays=30,
        tier=UserTier.BETA,
        expireAt=datetime.utcnow() + timedelta(days=14),
    ).model_dump(mode="json")
    payload["signature"] = hmacUtil.signPayload(payload)
    return base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


def main() -> int:
    rawInvite = makeInvite()
    print(f"[*] signed invite code ({len(rawInvite)} bytes)")

    _hr("1) POST /v1/auth/redeem")
    redeemResp = requests.post(
        f"{BASE}/v1/auth/redeem",
        json={
            "code": rawInvite,
            "deviceId": DEVICE_ID,
            "deviceName": "smoke-test",
            "displayName": "烟测用户",
        },
        timeout=10,
    )
    redeemBody = _ok("redeem", redeemResp)
    accessToken = redeemBody["tokens"]["accessToken"]
    refreshToken = redeemBody["tokens"]["refreshToken"]
    authHeader = {"Authorization": f"Bearer {accessToken}"}

    _hr("2) GET /v1/account/me")
    meResp = requests.get(f"{BASE}/v1/account/me", headers=authHeader, timeout=10)
    _ok("me", meResp)

    _hr("3) POST /v1/billing/preauth")
    preauthResp = requests.post(
        f"{BASE}/v1/billing/preauth",
        json={
            "actionType": "freq_analyze",
            "resourceUsed": 3200,
            "taskId": "task-smoke-001",
            "description": "smoke test",
        },
        headers={**authHeader, "Idempotency-Key": "idem-smoke-001"},
        timeout=10,
    )
    preauthBody = _ok("preauth", preauthResp)
    billId = preauthBody["billId"]

    _hr("4) POST /v1/billing/settle (realCost < estimated)")
    settleResp = requests.post(
        f"{BASE}/v1/billing/settle",
        json={"billId": billId, "realCost": 4, "resourceUsed": 3000},
        headers=authHeader,
        timeout=10,
    )
    _ok("settle", settleResp)

    _hr("5) GET /v1/account/bills")
    billsResp = requests.get(
        f"{BASE}/v1/account/bills?limit=10", headers=authHeader, timeout=10
    )
    _ok("bills", billsResp)

    _hr("6) POST /v1/auth/refresh(滚动续期)")
    refreshResp = requests.post(
        f"{BASE}/v1/auth/refresh",
        json={"refreshToken": refreshToken},
        headers={"X-Device-Id": DEVICE_ID},
        timeout=10,
    )
    refreshBody = _ok("refresh", refreshResp)
    newAccess = refreshBody["tokens"]["accessToken"]

    _hr("7) GET /v1/account/me(用新 access)")
    me2 = requests.get(
        f"{BASE}/v1/account/me", headers={"Authorization": f"Bearer {newAccess}"}, timeout=10
    )
    _ok("me-after-refresh", me2)

    print("\n[ALL OK] 烟测全链路通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
