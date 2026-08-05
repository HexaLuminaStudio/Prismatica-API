"""RequestId 中间件:每个请求分配唯一 id,贯穿日志与错误响应。"""
from __future__ import annotations

import uuid

from flask import Flask, g, request


def installRequestId(app: Flask) -> None:
    """注入 X-Request-Id(若无则生成)到 flask.g.requestId。"""

    @app.before_request
    def _bindRequestId():
        requestId = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        g.requestId = requestId

    @app.after_request
    def _injectResponseHeader(resp):
        if hasattr(g, "requestId"):
            resp.headers["X-Request-Id"] = g.requestId
        return resp
