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
    """账单列表(分页 + 过滤)。"""
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
    """账单详情(含用户 displayName)。"""
    result = getBillDetail(billId)
    data = AdminBillListItem(**result).model_dump(mode="json")
    return successEnvelope(data)


__all__ = ["bp"]
