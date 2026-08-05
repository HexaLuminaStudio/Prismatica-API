"""访问日志中间件:记录 method/path/status/latency/requestId。"""
from __future__ import annotations

import time

from flask import Flask, g, request
from loguru import logger


def installAccessLog(app: Flask) -> None:
    """在 before/after_request 钩子中记录访问日志。"""

    @app.before_request
    def _startTimer():
        g.startTs = time.perf_counter()

    @app.after_request
    def _logAccess(resp):
        try:
            latencyMs = (time.perf_counter() - g.startTs) * 1000.0
        except Exception:
            latencyMs = 0.0
        requestId = getattr(g, "requestId", "-")
        clientIp = request.headers.get("X-Forwarded-For", request.remote_addr or "-")
        logger.info(
            f"[Access] {request.method} {request.path} "
            f"status={resp.status_code} latency={latencyMs:.1f}ms "
            f"rid={requestId} ip={clientIp}"
        )
        return resp
