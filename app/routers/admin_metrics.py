"""/v1/admin/metrics/* 路由(2026-08-06 重构)。

端点:
    GET /v1/admin/metrics/summary    看板聚合 KPI
"""
from __future__ import annotations

from flask import Blueprint

from app.deps import requireAdminCookie
from app.errors import successEnvelope
from app.schemas.admin import AdminMetricsSummary
from app.services.admin_audit_service import metricsSummary

bp = Blueprint("admin_metrics", __name__, url_prefix="/v1/admin/metrics")


@bp.get("/summary")
@requireAdminCookie
def metricsSummaryRoute():
    """看板 KPI:用户总数 / 7 日活跃 / grant 总额 / 账单状态分布。"""
    data = AdminMetricsSummary(**metricsSummary()).model_dump()
    return successEnvelope(data)


__all__ = ["bp"]