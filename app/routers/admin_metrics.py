"""/v1/admin/metrics/* 路由(2026-08-06 重构)。

端点:
    GET /v1/admin/metrics/summary    看板聚合 KPI
"""

from __future__ import annotations

from flask import Blueprint

from app.deps import requireAdminCookie
from app.errors import successEnvelope
from app.schemas.admin import (
    AdminCodesKpi,
    AdminMetricsSummary,
    AdminSubscriptionDistributionResponse,
)
from app.services.admin_audit_service import (
    codesKpi,
    metricsSummary,
    subscriptionDistribution,
)

bp = Blueprint("admin_metrics", __name__, url_prefix="/v1/admin/metrics")


@bp.get("/summary")
@requireAdminCookie
def metricsSummaryRoute():
    """看板 KPI:用户总数 / 7 日活跃 / grant 总额 / 账单状态分布。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    responses:
      200:
        description: 看板 KPI
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    data = AdminMetricsSummary(**metricsSummary()).model_dump()
    return successEnvelope(data)


@bp.get("/subscription-distribution")
@requireAdminCookie
def subscriptionDistributionRoute():
    """按用户档位统计订阅分布。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    responses:
      200:
        description: 订阅分布统计
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    data = AdminSubscriptionDistributionResponse(**subscriptionDistribution()).model_dump()
    return successEnvelope(data)


@bp.get("/codes-kpi")
@requireAdminCookie
def codesKpiRoute():
    """兑换码看板 KPI。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    responses:
      200:
        description: 兑换码 KPI
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    data = AdminCodesKpi(**codesKpi()).model_dump()
    return successEnvelope(data)


__all__ = ["bp"]
