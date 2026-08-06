"""/healthz + /metrics + /openapi.json(公共端点)。"""
from __future__ import annotations

from flask import Blueprint, Response, current_app, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import getSettings
from app.db import pingDb
from app.errors import successEnvelope

bp = Blueprint("public", __name__)


@bp.get("/healthz")
def healthz():
    settings = getSettings()
    dbOk = pingDb()
    payload = {
        "status": "ok" if dbOk else "degraded",
        "service": settings.appName,
        "env": settings.env,
        "db": "up" if dbOk else "down",
    }
    code = 200 if dbOk else 503
    return successEnvelope(payload, httpStatus=code)


@bp.get("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


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
        methods = sorted(
            m for m in rule.methods if m in ("GET", "POST", "PUT", "DELETE", "PATCH")
        )
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
