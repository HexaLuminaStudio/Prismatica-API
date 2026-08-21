"""/v1/admin/bills* 路由(2026-08-06 新增)。

端点:
    GET /v1/admin/bills            账单列表(分页 + 过滤:status / userId / days)
    GET /v1/admin/bills/{billId}   账单详情(含用户 displayName)

业务由 admin_bill_service 承载。
"""

from __future__ import annotations

from flask import Blueprint, request

from app.deps import requireAdminCookie
from app.errors import ApiError, successEnvelope
from app.schemas.admin import AdminBillListItem, AdminBillListResponse
from app.services.admin_bill_service import getBillDetail, listBills

bp = Blueprint("admin_bills", __name__, url_prefix="/v1/admin/bills")


@bp.get("")
@requireAdminCookie
def listBillsRoute():
    """账单列表(分页 + 过滤)。

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
      - name: status
        in: query
        required: false
        schema: {type: string}
      - name: userId
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
        description: 账单列表
      400:
        description: limit/days 格式错误(BAD_REQUEST)
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
    """
    try:
        limit = int(request.args.get("limit", 50))
        daysRaw = request.args.get("days")
        days = int(daysRaw) if daysRaw else None
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"limit/days 格式错误: {e}") from e

    cursor = request.args.get("cursor")
    status = (request.args.get("status") or "").strip() or None
    userId = (request.args.get("userId") or "").strip() or None

    items, nextCursor = listBills(
        limit=limit,
        cursor=cursor,
        status=status,
        userId=userId,
        days=days,
    )
    data = AdminBillListResponse(
        items=[AdminBillListItem(**i) for i in items],
        nextCursor=nextCursor,
    ).model_dump(mode="json")
    return successEnvelope(data)


@bp.get("/<string:billId>")
@requireAdminCookie
def billDetailRoute(billId: str):
    """账单详情(含用户 displayName)。

    ---
    tags: [admin]
    security:
      - adminCookie: []
    parameters:
      - name: billId
        in: path
        required: true
        schema: {type: string}
    responses:
      200:
        description: 账单详情
      401:
        description: 未登录(ADMIN_LOGIN_REQUIRED)
      404:
        description: 账单不存在(NOT_FOUND)
    """
    result = getBillDetail(billId)
    data = AdminBillListItem(**result).model_dump(mode="json")
    return successEnvelope(data)


__all__ = ["bp"]
