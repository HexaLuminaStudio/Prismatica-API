"""Admin 用户管理 Pydantic 模型(2026-08-05 M2 B2)。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class AdminUserListItem(BaseModel):
    """GET /v1/admin/users 单条。"""

    userId: str
    displayName: str
    tier: str
    status: str
    balance: int
    totalSpent: int
    totalRecharged: int
    activatedAt: datetime
    lastSeenAt: Optional[datetime] = None


class AdminUserListResponse(BaseModel):
    """GET /v1/admin/users 响应(cursor 分页)。"""

    items: list[AdminUserListItem]
    nextCursor: Optional[str] = None
    total: int = 0


class AdminUserDetail(BaseModel):
    """GET /v1/admin/users/{userId} 详情(含完整字段)。"""

    userId: str
    displayName: str
    tier: str
    status: str
    balance: int
    frozenBalance: int
    totalSpent: int
    totalRecharged: int
    activatedAt: datetime
    expireAt: Optional[datetime] = None
    lastSeenAt: Optional[datetime] = None
    deviceCount: int = Field(default=0, description="已登录 device 数")


class RevokeSessionsRequest(BaseModel):
    """POST /v1/admin/users/{userId}/revoke-sessions 请求体(目前空)。"""

    reason: str = Field(default="", max_length=256)


class RevokeSessionsResponse(BaseModel):
    revokedCount: int
    userId: str


class AdminAuditItem(BaseModel):
    """GET /v1/admin/audit 单条。"""

    auditId: int
    actor: str
    action: str
    targetUser: Optional[str] = None
    details: Optional[dict] = None
    ip: Optional[str] = None
    createdAt: datetime


class AdminAuditResponse(BaseModel):
    items: list[AdminAuditItem]
    nextCursor: Optional[str] = None


class AdminAuditSummaryItem(BaseModel):
    """audit_summary group by action。"""

    action: str
    count: int


class AdminAuditSummaryResponse(BaseModel):
    items: list[AdminAuditSummaryItem]
    days: int
    total: int


class CodeLookupRequest(BaseModel):
    """GET /v1/admin/codes/lookup query 参数也支持,这里用于 body 备用。"""

    code: str = Field(..., min_length=1)


class CodeLookupResponse(BaseModel):
    """查询某个 RCH/INV/TRY 状态。"""

    codeKind: str
    codeHash: str
    consumedAt: Optional[datetime] = None
    consumedByUserId: Optional[str] = None
    rechargeAmount: Optional[int] = None


class AdminMetricsSummary(BaseModel):
    """GET /v1/admin/metrics-summary 响应。"""

    userCount: int
    sevenDayActive: int
    sevenDayGrantTotal: int
    billsPending: int
    billsSettledLast7Days: int
    billsRefundedLast7Days: int


__all__ = [
    "AdminUserListItem",
    "AdminUserListResponse",
    "AdminUserDetail",
    "RevokeSessionsRequest",
    "RevokeSessionsResponse",
    "AdminAuditItem",
    "AdminAuditResponse",
    "AdminAuditSummaryItem",
    "AdminAuditSummaryResponse",
    "CodeLookupRequest",
    "CodeLookupResponse",
    "AdminMetricsSummary",
]
