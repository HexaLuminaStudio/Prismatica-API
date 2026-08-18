"""管理后台定价中心路由。"""

from __future__ import annotations

from flask import Blueprint, g, request

from app.deps import getClientIp, requireAdminCookie, requireOwner
from app.errors import ApiError, successEnvelope
from app.services.admin_audit_service import recordAudit
from app.services.admin_pricing_service import createPricingDraft, getPricingOverview, publishPricingVersion

bp = Blueprint("admin_pricing", __name__, url_prefix="/v1/admin/pricing")


@bp.get("")
@requireAdminCookie
def pricingOverviewRoute():
    return successEnvelope(getPricingOverview())


@bp.post("/drafts")
@requireAdminCookie
@requireOwner
def createPricingDraftRoute():
    payload = request.get_json(silent=True) or {}
    rawRules = payload.get("rules")
    if not isinstance(rawRules, list):
        raise ApiError("BAD_REQUEST", "rules 必须是数组")
    actor = str(g.adminActor)
    result = createPricingDraft(actor, rawRules, str(payload.get("note", "")))
    recordAudit(
        actor=actor,
        action="pricing.draft.create",
        details={"versionCode": result["versionCode"], "ruleCount": len(rawRules)},
        ip=getClientIp(),
    )
    return successEnvelope(result, httpStatus=201)


@bp.post("/<string:versionCode>/publish")
@requireAdminCookie
@requireOwner
def publishPricingVersionRoute(versionCode: str):
    actor = str(g.adminActor)
    result = publishPricingVersion(versionCode, actor)
    recordAudit(
        actor=actor,
        action="pricing.version.publish",
        details={"versionCode": versionCode},
        ip=getClientIp(),
    )
    return successEnvelope(result)


__all__ = ["bp"]
