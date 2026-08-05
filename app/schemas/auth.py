"""认证相关 Pydantic 模型 — 对齐 PRD §5.2。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RedeemRequest(BaseModel):
    """POST /v1/auth/redeem 请求体。"""

    code: str = Field(..., description="INV/TRY/RCH 码(base64 签名载荷)")
    deviceId: str = Field(..., description="客户端设备 UUID")
    deviceName: str = Field(default="", description="设备名(脱敏)")
    displayName: str = Field(default="内测用户", description="用户显示名")


class UserOut(BaseModel):
    """user 子对象。"""

    userId: str
    displayName: str
    tier: str
    createdAt: datetime
    expireAt: datetime | None = None


class BalanceOut(BaseModel):
    """balance 子对象。"""

    balance: int
    frozenBalance: int
    totalSpent: int
    totalRecharged: int


class TokensOut(BaseModel):
    """tokens 子对象。"""

    accessToken: str
    refreshToken: str
    expiresIn: int


class RedeemResponse(BaseModel):
    """POST /v1/auth/redeem 成功响应。"""

    mode: str = Field(..., description="invite / trial / recharge")
    user: UserOut
    balance: BalanceOut
    tokens: TokensOut


class RefreshRequest(BaseModel):
    """POST /v1/auth/refresh 请求体。"""

    refreshToken: str = Field(..., description="opaque UUID")


class LogoutRequest(BaseModel):
    """POST /v1/auth/logout 请求体(可选 body)。"""

    refreshToken: str | None = None


__all__ = [
    "RedeemRequest",
    "UserOut",
    "BalanceOut",
    "TokensOut",
    "RedeemResponse",
    "RefreshRequest",
    "LogoutRequest",
]
