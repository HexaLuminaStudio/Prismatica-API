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
    """定价总览(当前生效版本 + 草稿列表)。

    ---
    tags: [pricing]
    security:
      - adminCookie: []
    responses:
      200:
        description: 定价总览
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                currentVersion: {type: string, nullable: true}
                publishedAt: {type: string, format: date-time, nullable: true}
                drafts:
                  type: array
                  items: {type: object}
            requestId: {type: string}
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    return successEnvelope(getPricingOverview())


@bp.post("/drafts")
@requireAdminCookie
@requireOwner
def createPricingDraftRoute():
    """创建定价规则草稿(仅 owner)。

    ---
    tags: [pricing]
    security:
      - adminCookie: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [rules]
            properties:
              rules:
                type: array
                items: {type: object}
                description: PricingRule 数组(actionType / baseCost / perUnit 等)
              note:
                type: string
                description: 变更说明(可选)
    responses:
      201:
        description: 草稿创建成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                versionCode: {type: string}
                ruleCount: {type: integer}
            requestId: {type: string}
      400:
        description: rules 必须是数组(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      403:
        description: 非 owner 无权限(FORBIDDEN)
    """
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
    """发布定价草稿为生效版本(仅 owner)。

    ---
    tags: [pricing]
    security:
      - adminCookie: []
    parameters:
      - name: versionCode
        in: path
        required: true
        schema:
          type: string
        description: 草稿版本号(来自 GET /v1/admin/pricing 的 drafts)
    responses:
      200:
        description: 发布成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                versionCode: {type: string}
                publishedAt: {type: string, format: date-time}
            requestId: {type: string}
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      403:
        description: 非 owner 无权限(FORBIDDEN)
      404:
        description: 草稿不存在(NOT_FOUND)
    """
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
