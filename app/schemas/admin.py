"""Admin 全部 Pydantic 模型(2026-08-06 重构):

按路由模块分组:
    auth    认证(login / me / change-password)
    users   用户管理(list / detail / patch / grant / revoke-sessions)
    codes   凭证签发(issue / list / lookup / revoke)
    audit   审计日志 / 看板
    metrics 看板聚合

所有响应 data 子结构在此集中定义;路由层直接 .model_dump(mode="json")。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ===========================================================================
# auth — 认证
# ===========================================================================


class AdminLoginRequest(BaseModel):
    """POST /v1/admin/auth/login 请求体。"""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class AdminMeResponse(BaseModel):
    """GET /v1/admin/auth/me 响应 data。"""

    userId: str
    username: str
    role: str
    status: str
    lastLoginAt: datetime | None = None


class AdminChangePasswordRequest(BaseModel):
    """POST /v1/admin/auth/change-password 请求体。"""

    oldPassword: str = Field(..., min_length=1)
    newPassword: str = Field(..., min_length=8, max_length=256)


class AdminChangePasswordResponse(BaseModel):
    success: bool = True


# ===========================================================================
# users — 用户管理
# ===========================================================================


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


class AdminUserListResponse(BaseModel):
    """GET /v1/admin/users 响应 data。"""

    items: list[AdminUserListItem]
    nextCursor: Optional[str] = None


class AdminUserDetail(BaseModel):
    """GET /v1/admin/users/{userId} 响应 data。"""

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
    deviceCount: int = 0


class AdminUpdateUserRequest(BaseModel):
    """PATCH /v1/admin/users/{userId} 请求体。"""

    tier: str = Field(..., description="guest / trial / beta / beta_pro / paid")
    status: Optional[str] = Field(
        default=None, description="active / suspended / expired(可选)"
    )


class AdminUpdateUserResponse(BaseModel):
    userId: str
    tier: str
    status: str


class AdminGrantBalanceRequest(BaseModel):
    """POST /v1/admin/users/{userId}/grant 请求体。"""

    amount: int = Field(..., gt=0)
    note: str = Field(default="", max_length=256)


class AdminGrantBalanceResponse(BaseModel):
    userId: str
    newBalance: int


class AdminRevokeSessionsRequest(BaseModel):
    """POST /v1/admin/users/{userId}/revoke-sessions 请求体。"""

    reason: str = Field(default="", max_length=256)


class AdminRevokeSessionsResponse(BaseModel):
    userId: str
    revokedCount: int


# ===========================================================================
# codes — 凭证签发
# ===========================================================================


class AdminIssueCodesRequest(BaseModel):
    """POST /v1/admin/codes 请求体。"""

    kind: str = Field(..., description="invite / trial / recharge")
    count: int = Field(..., ge=1, le=1000)
    grantedBalance: int = Field(default=100, ge=0)
    grantedDays: int = Field(default=30, ge=1)
    tier: str = Field(default="beta")
    amount: int = Field(default=0, ge=0, description="仅 recharge 码使用")
    expireDays: int = Field(default=14, ge=1)


class AdminIssuedCodeItem(BaseModel):
    """单条凭证(issue 时一次性返回明文 + 签名载荷)。"""

    codeHash: str
    code: str = Field(..., description="仅本次签发响应可见明文")
    signedPayload: str = Field(..., description="base64(JSON payload+signature)")
    codeKind: str
    status: str
    grantedBalance: Optional[int] = None
    grantedDays: Optional[int] = None
    tier: Optional[str] = None
    amount: Optional[int] = None
    issuedBy: str
    issuedAt: datetime
    expireAt: datetime


class AdminIssueCodesResponse(BaseModel):
    items: list[AdminIssuedCodeItem]


class AdminCodeListItem(BaseModel):
    """GET /v1/admin/codes 单条(列表不含明文 code)。"""

    codeHash: str
    codeKind: str
    status: str
    grantedBalance: Optional[int] = None
    grantedDays: Optional[int] = None
    tier: Optional[str] = None
    amount: Optional[int] = None
    issuedBy: str
    issuedAt: datetime
    expireAt: Optional[datetime] = None
    consumedAt: Optional[datetime] = None
    consumedByUserId: Optional[str] = None
    consumedIp: Optional[str] = None


class AdminCodeListResponse(BaseModel):
    items: list[AdminCodeListItem]
    nextCursor: Optional[str] = None


class AdminCodeLookupResponse(BaseModel):
    """GET /v1/admin/codes/lookup 响应 data。"""

    codeKind: str
    codeHash: str
    status: str
    consumedAt: Optional[datetime] = None
    consumedByUserId: Optional[str] = None
    rechargeAmount: Optional[int] = None


class AdminCodeRevokeResponse(BaseModel):
    codeHash: str
    status: str


# ===========================================================================
# audit — 审计日志
# ===========================================================================


class AdminAuditItem(BaseModel):
    auditId: int
    actor: str
    action: str
    targetUser: Optional[str] = None
    details: Optional[dict[str, Any]] = None
    ip: Optional[str] = None
    createdAt: datetime


class AdminAuditResponse(BaseModel):
    items: list[AdminAuditItem]
    nextCursor: Optional[str] = None


class AdminAuditSummaryItem(BaseModel):
    action: str
    count: int


class AdminAuditSummaryResponse(BaseModel):
    items: list[AdminAuditSummaryItem]
    days: int
    total: int


# ===========================================================================
# metrics — 看板聚合
# ===========================================================================


class AdminMetricsSummary(BaseModel):
    """GET /v1/admin/metrics/summary 响应 data。"""

    userCount: int
    sevenDayActive: int
    sevenDayGrantTotal: int
    billsPending: int
    billsSettledLast7Days: int
    billsRefundedLast7Days: int


__all__ = [
    # auth
    "AdminLoginRequest",
    "AdminMeResponse",
    "AdminChangePasswordRequest",
    "AdminChangePasswordResponse",
    # users
    "AdminUserListItem",
    "AdminUserListResponse",
    "AdminUserDetail",
    "AdminUpdateUserRequest",
    "AdminUpdateUserResponse",
    "AdminGrantBalanceRequest",
    "AdminGrantBalanceResponse",
    "AdminRevokeSessionsRequest",
    "AdminRevokeSessionsResponse",
    # codes
    "AdminIssueCodesRequest",
    "AdminIssuedCodeItem",
    "AdminIssueCodesResponse",
    "AdminCodeListItem",
    "AdminCodeListResponse",
    "AdminCodeLookupResponse",
    "AdminCodeRevokeResponse",
    # audit
    "AdminAuditItem",
    "AdminAuditResponse",
    "AdminAuditSummaryItem",
    "AdminAuditSummaryResponse",
    # metrics
    "AdminMetricsSummary",
]