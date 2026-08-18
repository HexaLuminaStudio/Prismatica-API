"""/healthz + /metrics + /openapi.json(公共端点)。"""

from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import getSettings
from app.db import getDb
from app.errors import successEnvelope
from app.health import buildHealthPayload
from app.services.pricing import getPricingService

bp = Blueprint("public", __name__)


@bp.get("/healthz")
def healthz():
    payload, code = buildHealthPayload()
    return successEnvelope(payload, httpStatus=code)


@bp.get("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@bp.get("/v1/pricing/catalog")
def pricingCatalog():
    """公开价格目录；客户端按 version 每 30 秒检查更新。"""
    with getDb() as db:
        return successEnvelope(getPricingService().publicCatalog(db))


@bp.get("/openapi.json")
def openapi():
    """最小化 OpenAPI 3.0 描述(由 routers 列表拼接)。"""
    rules = current_app.url_map.iter_rules()
    paths: dict = {}
    for rule in rules:
        if rule.endpoint == "static":
            continue
        path = rule.rule
        # 2026-08-05 M2:同时输出 /admin/* 与 /v1/admin/* 与 /v1/*;
        # 排除 /healthz / /openapi.json / /metrics /admin/health 等基础设施路径。
        if path in ("/healthz", "/openapi.json", "/metrics", "/admin/health"):
            continue
        if not (path.startswith("/v1/") or path.startswith("/admin/")):
            continue
        methods = sorted(m for m in rule.methods if m in ("GET", "POST", "PUT", "DELETE", "PATCH"))
        entry: dict = {}
        for method in methods:
            entry[method.lower()] = {
                "summary": rule.endpoint,
                "responses": {
                    "200": {"description": "OK"},
                    "4xx": {"description": "Client Error"},
                    "5xx": {"description": "Server Error"},
                },
            }
        paths[path] = entry
    spec = {
        "openapi": "3.0.3",
        "info": {
            "title": getSettings().appName,
            "version": "0.1.0",
            "description": "Prismatica 云端后端 API(对齐 PRD v2)",
        },
        "servers": [{"url": "/"}],
        "paths": paths,
    }
    return jsonify(spec)
