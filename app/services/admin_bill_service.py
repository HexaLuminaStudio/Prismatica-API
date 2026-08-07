"""Admin 账单服务(2026-08-06 新增):

- listBills(limit, cursor, status?, userId?, days?) → (items, nextCursor)
    - 列表含用户 displayName(LEFT JOIN user_accounts)
- getBillDetail(billId) → dict(单条 + 用户 displayName)

所有时间过滤基于 created_at(naive UTC,与库内其它服务一致)。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db import getDb
from app.errors import ApiError
from app.models import Bill, UserAccount

_VALID_STATUS = {"pending", "settled", "refunded", "expired"}


def _parseCursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e


def _toItem(row: Bill, displayName: str | None) -> dict[str, Any]:
    """把 Bill ORM 行转为字典(与 schema AdminBillListItem 对齐)。"""
    return {
        "billId": row.billId,
        "userId": str(row.userId),
        "displayName": displayName,
        "actionType": row.feature,
        "actionDisplayName": row.feature,
        "estimatedCost": int(row.estimatedCost or 0),
        "realCost": int(row.actualCost or 0),
        "resourceUsed": 0,
        "balanceBefore": 0,
        "balanceAfter": 0,
        "status": row.status,
        "taskId": "",
        "description": row.description,
        "idempotencyKey": row.idempotencyKey,
        "createdAt": row.createdAt,
        "settledAt": row.settledAt,
    }


def listBills(
    limit: int = 50,
    cursor: str | None = None,
    status: str | None = None,
    userId: str | None = None,
    days: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """账单分页查询 → 返回 (items, nextCursor)。

    过滤:
        - status: pending / settled / refunded
        - userId: 精确匹配
        - days: 最近 N 天(created_at)
    """
    limit = max(1, min(200, limit))
    if status and status not in _VALID_STATUS:
        raise ApiError("BAD_REQUEST", f"status 必须为 {sorted(_VALID_STATUS)} 之一")

    with getDb() as db:
        stmt = (
            select(Bill, UserAccount.displayName)
            .outerjoin(UserAccount, UserAccount.id == Bill.userId)
            .order_by(Bill.createdAt.desc())
        )
        cursorDt = _parseCursor(cursor)
        if cursorDt is not None:
            stmt = stmt.where(Bill.createdAt < cursorDt)
        if status:
            stmt = stmt.where(Bill.status == status)
        if userId:
            try:
                numericUserId = int(userId)
            except ValueError as error:
                raise ApiError("BAD_REQUEST", "userId 必须为数字") from error
            stmt = stmt.where(Bill.userId == numericUserId)
        if days:
            since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=int(days))
            stmt = stmt.where(Bill.createdAt >= since)
        stmt = stmt.limit(limit + 1)

        rows = db.execute(stmt).all()
        nextCursor: str | None = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1][0].createdAt.isoformat()
            rows = rows[:limit]

        items = [_toItem(row, displayName) for row, displayName in rows]
        return items, nextCursor


def getBillDetail(billId: str) -> dict[str, Any]:
    """账单详情(含用户 displayName)。"""
    with getDb() as db:
        row = db.execute(select(Bill).where(Bill.billId == billId)).scalar_one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", "账单不存在")
        user = db.get(UserAccount, row.userId)
        return _toItem(row, user.displayName if user else None)


__all__ = ["listBills", "getBillDetail"]
