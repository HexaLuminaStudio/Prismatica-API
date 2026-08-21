"""/healthz + /metrics + /openapi.json(公共端点)。"""

from __future__ import annotations

from flask import Blueprint, Response, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.db import getDb
from app.errors import successEnvelope
from app.health import buildHealthPayload
from app.services.pricing import getPricingService
from app.swagger_docs import getOpenApiSpec

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
    """OpenAPI 3.0 描述(Flasgger 生成,与 /apidocs/ 交互文档同源)。"""
    return jsonify(getOpenApiSpec())
