"""Admin cookie session 工具(2026-08-05 M2 B1)

策略:
    - cookie 名为 `prismatica_admin`(HttpOnly + SameSite=Lax)
    - cookie value = base64url(payload).<HMAC-SHA256 hex>
    - payload = JSON {"userId":"...", "username":"...", "ts": epoch_seconds}
    - 校验:HMAC 用 SESSION_SECRET;过期看 ts + maxAge(Settings.adminCookieMaxAgeSec)

依赖:
    - app.config.getSettings() 提供 adminCookieSecret / adminCookieMaxAgeSec

不依赖 flask 上下文(纯函数),路由层与装饰器都能调用。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from flask import Response

from app.config import getSettings

COOKIE_NAME = "prismatica_admin"


def _b64urlEncode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64urlDecode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payloadBytes: bytes, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"), payloadBytes, hashlib.sha256
    ).hexdigest()


def makeSessionValue(userId: str, username: str) -> str:
    """生成 cookie value。"""
    settings = getSettings()
    payload = {
        "userId": userId,
        "username": username,
        "ts": int(time.time()),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sig = _sign(raw, settings.adminCookieSecret)
    return f"{_b64urlEncode(raw)}.{sig}"


def verifySessionValue(value: str) -> dict[str, Any] | None:
    """校验 cookie value → 返回 payload dict(失败返回 None)。"""
    if not value or "." not in value:
        return None
    try:
        encodedPayload, sig = value.rsplit(".", 1)
        raw = _b64urlDecode(encodedPayload)
        settings = getSettings()
        expected = _sign(raw, settings.adminCookieSecret)
        if not hmac.compare_digest(expected, sig):
            return None
        payload = json.loads(raw.decode("utf-8"))
        # 过期判断
        ts = int(payload.get("ts") or 0)
        if int(time.time()) - ts > settings.adminCookieMaxAgeSec:
            return None
        return payload
    except Exception:  # noqa: BLE001
        return None


def setSessionCookie(resp: Response, userId: str, username: str) -> Response:
    """给 Response 注入 Set-Cookie,HttpOnly + maxAge。

    SameSite/Secure 策略(2026-08-06 M2 cors-fix):
        - 默认(同源 HTTP 开发模式):Secure=False, SameSite=Lax
        - 跨端口 / HTTPS 生产:Secure=True, SameSite=None
          (SameSite=None 必须配 Secure,否则浏览器拒绝 Set-Cookie)
    """
    settings = getSettings()
    value = makeSessionValue(userId=userId, username=username)
    resp.set_cookie(
        COOKIE_NAME,
        value,
        max_age=settings.adminCookieMaxAgeSec,
        httponly=True,
        secure=settings.adminCookieSecure,
        samesite="None" if settings.adminCookieSecure else "Lax",
        path="/",
    )
    return resp


def clearSessionCookie(resp: Response) -> Response:
    """清 cookie。"""
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


def readSessionCookie() -> dict[str, Any] | None:
    """从当前 flask request 读 cookie 并校验。失败返回 None。"""
    from flask import request

    value = request.cookies.get(COOKIE_NAME)
    return verifySessionValue(value or "")


__all__ = [
    "COOKIE_NAME",
    "makeSessionValue",
    "verifySessionValue",
    "setSessionCookie",
    "clearSessionCookie",
    "readSessionCookie",
]
