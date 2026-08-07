"""P0-A 账号域 Pydantic 模型。

涵盖:
    - /v1/account/me 的完整响应(用户 + 余额 + 订阅)
    - 设备管理 / 注销请求响应
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# /me
# ---------------------------------------------------------------------------


class SubscriptionOut(BaseModel):
    """单条订阅快照(供 /me 嵌套)。"""

    subscriptionId: int
    planCode: str
    status: str
    startedAt: datetime
    currentPeriodStart: datetime
    currentPeriodEnd: datetime
    expiresAt: datetime
    autoRenew: bool
    monthlyQuota: int


class MeOut(BaseModel):
    """GET /v1/account/me — 完整用户态。

    字段:
        - userId / email / displayName / tier / status:身份
        - balance / reserved / available:积分
        - subscription:当前活跃订阅(None 表示 free)
        - failedLoginCount / lockedUntil:风控信息
        - createdAt:注册时间
    """

    userId: int
    email: str
    displayName: str
    tier: str
    status: str

    balance: int
    reserved: int
    available: int

    subscription: SubscriptionOut | None = None
    emailVerified: bool = False

    failedLoginCount: int = 0
    lockedUntil: datetime | None = None
    createdAt: datetime


class MePatchRequest(BaseModel):
    """PATCH /v1/account/me — 改名。

    为避免枚举(目前只允许改 displayName),只放一个字段;后续如允许
    改头像 / 简介,可在此模型添加。
    """

    displayName: str = Field(..., min_length=0, max_length=64)


class MePatchResponse(BaseModel):
    userId: int
    displayName: str
    updatedAt: datetime


# ---------------------------------------------------------------------------
# 设备
# ---------------------------------------------------------------------------


class DeviceOut(BaseModel):
    """单条设备快照。"""

    deviceId: int
    devicePublicId: str
    deviceName: str
    platform: str
    status: str
    firstSeenAt: datetime
    lastSeenAt: datetime
    revokedAt: datetime | None = None
    isCurrent: bool = Field(default=False, description="是否当前请求设备")


class DeviceListResponse(BaseModel):
    items: list[DeviceOut]
    maxActive: int
    activeCount: int


# ---------------------------------------------------------------------------
# 注销
# ---------------------------------------------------------------------------


class DeleteAccountRequest(BaseModel):
    """POST /v1/account/delete — 软删请求(30 天后硬删)。"""

    password: str = Field(..., min_length=1)
    confirm: bool = Field(default=False, description="必须显式确认 true")


class DeleteAccountResponse(BaseModel):
    userId: int
    status: str
    scheduledHardDeleteAt: datetime
    revokedRefreshTokens: int


# ---------------------------------------------------------------------------
# 订阅列表(供 M6 单独列表)
# ---------------------------------------------------------------------------


class SubscriptionListResponse(BaseModel):
    items: list[SubscriptionOut]
    nextCursor: str | None = None


__all__ = [
    "SubscriptionOut",
    "MeOut",
    "MePatchRequest",
    "MePatchResponse",
    "DeviceOut",
    "DeviceListResponse",
    "DeleteAccountRequest",
    "DeleteAccountResponse",
    "SubscriptionListResponse",
]
