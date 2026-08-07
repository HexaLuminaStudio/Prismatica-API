"""后端健康检查响应构造。"""
from __future__ import annotations

from app.config import getSettings
from app.db import pingDb


def buildHealthPayload() -> tuple[dict[str, str], int]:
    """构造统一健康检查响应。"""
    settings = getSettings()
    dbOk = pingDb()
    payload = {
        "status": "ok" if dbOk else "degraded",
        "service": settings.appName,
        "env": settings.env,
        "db": "up" if dbOk else "down",
        "version": settings.appVersion,
        "build": settings.buildId,
        "commit": settings.gitCommit,
    }
    return payload, 200 if dbOk else 503
