# coding: utf-8
"""用户/账单 Pydantic 模型 — 对齐 PRD §5.3。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class UserAccountOut(BaseModel):
    """GET /v1/account/me 响应。"""

    userId: str
    displayName: str
    tier: str
    balance: int
    frozenBalance: int
    totalSpent: int
    totalRecharged: int
    activatedAt: datetime
    expireAt: Optional[datetime] = None


class BillOut(BaseModel):
    """GET /v1/account/bills 单条。"""

    billId: str
    actionType: str
    actionDisplayName: str
    estimatedCost: int
    realCost: int
    resourceUsed: int
    balanceBefore: int
    balanceAfter: int
    status: str
    taskId: str
    description: str
    createdAt: datetime
    settledAt: Optional[datetime] = None


class BillListResponse(BaseModel):
    """GET /v1/account/bills 响应(cursor 分页)。"""

    items: list[BillOut]
    nextCursor: Optional[str] = None


__all__ = ["UserAccountOut", "BillOut", "BillListResponse"]