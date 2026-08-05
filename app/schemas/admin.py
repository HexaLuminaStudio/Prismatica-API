"""Admin Pydantic 模型 — 对齐 PRD §5.5。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AdminGrantRequest(BaseModel):
    """POST /v1/admin/grant 请求体。"""

    userId: str
    amount: int = Field(..., gt=0)
    note: str = Field(default="客服补偿")


class AdminGrantResponse(BaseModel):
    """POST /v1/admin/grant 响应。"""

    userId: str
    newBalance: int


class AdminIssueCodesRequest(BaseModel):
    """POST /v1/admin/issue-codes 请求体。"""

    kind: str = Field(..., description="invite / trial / recharge")
    count: int = Field(..., ge=1, le=1000)
    grantedBalance: int = Field(default=100, ge=0)
    grantedDays: int = Field(default=30, ge=1)
    tier: str = Field(default="beta")
    amount: int = Field(default=0, ge=0, description="仅 recharge 码使用")
    expireDays: int = Field(default=14, ge=1)


class AdminIssueCodesResponse(BaseModel):
    """POST /v1/admin/issue-codes 响应。"""

    codes: list[str]


class AdminUpdateUserTierRequest(BaseModel):
    """POST /v1/admin/users/{userId}/tier 请求体(2026-08-05 M2 B2)。"""

    tier: str = Field(..., description="tier 名:guest/trial/beta/beta_pro/paid")
    status: str = Field(default="", description="可选:active/suspended/expired")


__all__ = [
    "AdminGrantRequest",
    "AdminGrantResponse",
    "AdminIssueCodesRequest",
    "AdminIssueCodesResponse",
    "AdminUpdateUserTierRequest",
]  # noqa: E501
