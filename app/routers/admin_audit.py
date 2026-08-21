"""/v1/admin/audit* 路由(2026-08-06 重构)。

端点:
    GET /v1/admin/audit            审计日志列表
    GET /v1/admin/audit/summary    按 action group by + count
"""

from __future__ import annotations

from flask import Blueprint, request

from app.deps import requireAdminCookie
from app.errors import ApiError, successEnvelope
from app.schemas.admin import (
    AdminAuditItem,
    AdminAuditResponse,
    AdminAuditSummaryItem,
    AdminAuditSummaryResponse,
)
from app.services.admin_audit_service import auditSummary, listAudit

bp = Blueprint("admin_audit", __name__, url_prefix="/v1/admin/audit")


@bp.get("")
@requireAdminCookie
def listAuditRoute():
    """审计日志查询(分页 + 时间范围 + 过滤)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: limit
        in: query
        required: false
        schema: {type: integer, default: 50}
      - name: cursor
        in: query
        required: false
        schema: {type: string}
      - name: action
        in: query
        required: false
        schema: {type: string}
      - name: actor
        in: query
        required: false
        schema: {type: string}
      - name: targetUser
        in: query
        required: false
        schema: {type: string}
      - name: days
        in: query
        required: false
        schema: {type: integer}
        description: 最近 N 天
    responses:
      200:
        description: 审计日志列表
      400:
        description: limit 格式错误(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"limit 格式错误: {e}") from e

    cursor = request.args.get("cursor")
    action = (request.args.get("action") or "").strip() or None
    actor = (request.args.get("actor") or "").strip() or None
    targetUser = (request.args.get("targetUser") or "").strip() or None
    days = request.args.get("days")
    daysInt = int(days) if days else None

    items, nextCursor = listAudit(
        limit=limit,
        cursor=cursor,
        action=action,
        actor=actor,
        targetUser=targetUser,
        days=daysInt,
    )
    data = AdminAuditResponse(
        items=[AdminAuditItem(**i) for i in items],
        nextCursor=nextCursor,
    ).model_dump(mode="json")
    return successEnvelope(data)


@bp.get("/summary")
@requireAdminCookie
def auditSummaryRoute():
    """看板用 group by action + count。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: days
        in: query
        required: false
        schema: {type: integer, default: 7}
    responses:
      200:
        description: 审计汇总
      400:
        description: days 格式错误(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    try:
        days = int(request.args.get("days", 7))
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"days 格式错误: {e}") from e

    summary = auditSummary(days)
    data = AdminAuditSummaryResponse(
        items=[AdminAuditSummaryItem(**i) for i in summary["items"]],
        days=summary["days"],
        total=summary["total"],
    ).model_dump()
    return successEnvelope(data)


__all__ = ["bp"]
