# coding: utf-8
"""/v1/account/* 路由:me / bills。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from flask import Blueprint, g, jsonify, request
from pydantic import ValidationError
from sqlalchemy import select

from app.db import getDb
from app.deps import requireAuth
from app.errors import ApiError
from app.models import Bill, UserAccount, UserBalance
from app.schemas.user import BillListResponse, BillOut, UserAccountOut

bp = Blueprint("account", __name__, url_prefix="/v1/account")


@contextmanager
def _sessionCtx():
    with getDb() as db:
        yield db


@bp.get("/me")
@requireAuth
def getMe():
    with _sessionCtx() as db:
        user = db.get(UserAccount, g.userId)
        balance = db.get(UserBalance, g.userId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在", httpStatus=404)
        if balance is None:
            balance = UserBalance(userId=user.userId)
        resp = UserAccountOut(
            userId=user.userId,
            displayName=user.displayName,
            tier=user.tier,
            balance=balance.balance,
            frozenBalance=balance.frozenBalance,
            totalSpent=balance.totalSpent,
            totalRecharged=balance.totalRecharged,
            activatedAt=user.activatedAt,
            expireAt=user.expireAt,
        )
        return jsonify(resp.model_dump(mode="json"))


@bp.get("/bills")
@requireAuth
def getBills():
    limit = int(request.args.get("limit", 50))
    limit = max(1, min(200, limit))
    cursor = request.args.get("cursor")  # createdAt ISO 字符串(分页锚点)

    with _sessionCtx() as db:
        stmt = select(Bill).where(Bill.userId == g.userId)
        if cursor:
            try:
                cursorDt = datetime.fromisoformat(cursor.replace("Z", "+00:00"))
            except ValueError as e:
                raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e
            stmt = stmt.where(Bill.createdAt < cursorDt)
        stmt = stmt.order_by(Bill.createdAt.desc()).limit(limit + 1)
        rows = db.execute(stmt).scalars().all()

        nextCursor: Optional[str] = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1].createdAt.isoformat()
            rows = rows[:limit]

        items = [
            BillOut(
                billId=r.billId,
                actionType=r.actionType,
                actionDisplayName=r.actionDisplayName,
                estimatedCost=r.estimatedCost,
                realCost=r.realCost,
                resourceUsed=r.resourceUsed,
                balanceBefore=r.balanceBefore,
                balanceAfter=r.balanceAfter,
                status=r.status,
                taskId=r.taskId,
                description=r.description,
                createdAt=r.createdAt,
                settledAt=r.settledAt,
            )
            for r in rows
        ]
        return jsonify(BillListResponse(items=items, nextCursor=nextCursor).model_dump(mode="json"))