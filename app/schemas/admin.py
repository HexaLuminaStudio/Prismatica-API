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
from typing import Any

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
    email: str | None = None
    displayName: str
    tier: str
    status: str
    balance: int
    totalSpent: int
    totalRecharged: int
    activatedAt: datetime
    registeredAt: datetime | None = None
    lastSeenAt: datetime | None = None
    deviceCount: int = 0
    deletedAt: datetime | None = None


class AdminUserListResponse(BaseModel):
    """GET /v1/admin/users 响应 data。"""

    items: list[AdminUserListItem]
    nextCursor: str | None = None


class AdminUserDetail(BaseModel):
    """GET /v1/admin/users/{userId} 响应 data。"""

    userId: str
    email: str | None = None
    displayName: str
    tier: str
    status: str
    balance: int
    frozenBalance: int
    totalSpent: int
    totalRecharged: int
    lifetimeGrant: int = 0
    lifetimeConsumed: int = 0
    activatedAt: datetime
    registeredAt: datetime | None = None
    expireAt: datetime | None = None
    lastSeenAt: datetime | None = None
    deviceCount: int = 0
    deletedAt: datetime | None = None


class AdminCreateUserRequest(BaseModel):
    """POST /v1/admin/users 请求体。"""

    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=10, max_length=256)
    displayName: str = Field(default="", max_length=64)
    tier: str = Field(default="free")
    status: str = Field(default="active")


class AdminCreateUserResponse(AdminUserDetail):
    """POST /v1/admin/users 响应 data。"""


class AdminUpdateUserRequest(BaseModel):
    """PATCH /v1/admin/users/{userId} 请求体。"""

    tier: str | None = Field(default=None, description="free / pro / team / guest / trial / beta / beta_pro / paid")
    status: str | None = Field(default=None, description="active / paused / banned / deleted(可选)")
    email: str | None = Field(default=None, min_length=3, max_length=254)
    displayName: str | None = Field(default=None, max_length=64)


class AdminUpdateUserResponse(BaseModel):
    userId: str
    tier: str | None = None
    status: str | None = None
    email: str | None = None
    displayName: str | None = None


class AdminResetUserPasswordResponse(BaseModel):
    userId: str
    newPassword: str


class AdminDeleteUserResponse(BaseModel):
    userId: str
    deletedAt: datetime


class AdminBatchUsersRequest(BaseModel):
    action: str = Field(..., description="update_status / reset_password / delete")
    userIds: list[str] = Field(..., min_length=1, max_length=200)
    status: str | None = Field(default=None)


class AdminBatchUsersResponse(BaseModel):
    action: str
    successCount: int
    failedCount: int
    items: list[dict[str, Any]]


class AdminUserSubscriptionItem(BaseModel):
    subscriptionId: str
    planCode: str
    status: str
    startedAt: datetime
    currentPeriodStart: datetime
    currentPeriodEnd: datetime
    monthlyQuota: int
    autoRenew: bool


class AdminUserSubscriptionsResponse(BaseModel):
    items: list[AdminUserSubscriptionItem]


class AdminUserDeviceItem(BaseModel):
    deviceId: str
    deviceName: str
    platform: str
    status: str
    lastSeenAt: datetime
    createdAt: datetime


class AdminUserDevicesResponse(BaseModel):
    items: list[AdminUserDeviceItem]


class AdminRevokeUserDeviceResponse(BaseModel):
    deviceId: str
    status: str


class AdminUserLedgerItem(BaseModel):
    ledgerId: str
    type: str
    amount: int
    source: str
    refId: str | None = None
    note: str
    createdAt: datetime


class AdminUserLedgerResponse(BaseModel):
    items: list[AdminUserLedgerItem]


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
    tier: str = Field(default="pro")
    amount: int = Field(default=0, ge=0, description="仅 recharge 码使用")
    expireDays: int = Field(default=14, ge=1)


class AdminIssuedCodeItem(BaseModel):
    """单条凭证(issue 时一次性返回明文 + 签名载荷)。"""

    codeHash: str
    code: str = Field(..., description="仅本次签发响应可见明文")
    signedPayload: str = Field(..., description="base64(JSON payload+signature)")
    codeKind: str
    status: str
    grantedBalance: int | None = None
    grantedDays: int | None = None
    tier: str | None = None
    amount: int | None = None
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
    grantedBalance: int | None = None
    grantedDays: int | None = None
    tier: str | None = None
    amount: int | None = None
    issuedBy: str
    issuedAt: datetime
    expireAt: datetime | None = None
    consumedAt: datetime | None = None
    consumedByUserId: str | None = None
    consumedIp: str | None = None


class AdminCodeListResponse(BaseModel):
    items: list[AdminCodeListItem]
    nextCursor: str | None = None


class AdminCodeLookupResponse(BaseModel):
    """GET /v1/admin/codes/lookup 响应 data。"""

    codeKind: str
    codeHash: str
    status: str
    consumedAt: datetime | None = None
    consumedByUserId: str | None = None
    rechargeAmount: int | None = None


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
    targetUser: str | None = None
    details: dict[str, Any] | None = None
    ip: str | None = None
    createdAt: datetime


class AdminAuditResponse(BaseModel):
    items: list[AdminAuditItem]
    nextCursor: str | None = None


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


# ===========================================================================
# bills — 账单管理(2026-08-06 新增)
# ===========================================================================


class AdminBillListItem(BaseModel):
    """GET /v1/admin/bills 单条。"""

    billId: str
    userId: str
    displayName: str | None = None
    actionType: str
    actionDisplayName: str
    estimatedCost: int
    realCost: int
    resourceUsed: int
    balanceBefore: int
    balanceAfter: int
    status: str = Field(..., description="pending / settled / refunded")
    taskId: str
    description: str
    idempotencyKey: str | None = None
    createdAt: datetime
    settledAt: datetime | None = None


class AdminBillListResponse(BaseModel):
    items: list[AdminBillListItem]
    nextCursor: str | None = None


# ===========================================================================
# admins — 管理员账号管理(2026-08-06 M3 新增,owner-only)
# ===========================================================================


class AdminAccountListItem(BaseModel):
    """GET /v1/admin/admins 单条。"""

    userId: str
    username: str
    role: str = Field(..., description="owner / admin")
    status: str = Field(..., description="active / locked")
    lastLoginAt: datetime | None = None
    failedAttempts: int = 0
    createdAt: datetime


class AdminAccountListResponse(BaseModel):
    items: list[AdminAccountListItem]
    nextCursor: str | None = None


class AdminCreateAdminRequest(BaseModel):
    """POST /v1/admin/admins 请求体。"""

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    role: str = Field(default="admin", description="owner / admin")


class AdminCreateAdminResponse(BaseModel):
    userId: str
    username: str
    role: str
    status: str
    createdAt: datetime


class AdminUpdateAdminRequest(BaseModel):
    """PATCH /v1/admin/admins/{userId} 请求体。"""

    status: str | None = Field(default=None, description="active / locked")
    role: str | None = Field(default=None, description="owner / admin")


class AdminUpdateAdminResponse(BaseModel):
    userId: str
    role: str | None = None
    status: str | None = None


class AdminResetPasswordResponse(BaseModel):
    """POST /v1/admin/admins/{userId}/reset-password 响应 data。"""

    userId: str
    newPassword: str = Field(..., description="一次性明文")


class AdminDeleteAdminResponse(BaseModel):
    """DELETE /v1/admin/admins/{userId} 响应 data。"""

    userId: str
    deletedAt: datetime


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
    "AdminCreateUserRequest",
    "AdminCreateUserResponse",
    "AdminUpdateUserRequest",
    "AdminUpdateUserResponse",
    "AdminResetUserPasswordResponse",
    "AdminDeleteUserResponse",
    "AdminBatchUsersRequest",
    "AdminBatchUsersResponse",
    "AdminUserSubscriptionItem",
    "AdminUserSubscriptionsResponse",
    "AdminUserDeviceItem",
    "AdminUserDevicesResponse",
    "AdminRevokeUserDeviceResponse",
    "AdminUserLedgerItem",
    "AdminUserLedgerResponse",
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
    # bills (2026-08-06 新增)
    "AdminBillListItem",
    "AdminBillListResponse",
    # admins (2026-08-06 M3)
    "AdminAccountListItem",
    "AdminAccountListResponse",
    "AdminCreateAdminRequest",
    "AdminCreateAdminResponse",
    "AdminUpdateAdminRequest",
    "AdminUpdateAdminResponse",
    "AdminResetPasswordResponse",
    "AdminDeleteAdminResponse",
]
