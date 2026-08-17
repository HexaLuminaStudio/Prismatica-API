"""认证相关 Pydantic 模型 — 对齐 PRD §5.2。"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from app.schemas.base import UtcResponseModel as BaseModel


def _normalizeEmail(value: str) -> str:
    normalized = value.strip().lower()
    local, separator, domain = normalized.partition("@")
    if not separator or not local or not domain or "." not in domain or len(normalized) > 254:
        raise ValueError("邮箱格式不正确")
    return normalized


class RegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=10, max_length=256)
    displayName: str = Field(default="", max_length=64)

    _emailValidator = field_validator("email")(_normalizeEmail)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=256)
    deviceId: str = Field(min_length=1, max_length=64)
    deviceName: str = Field(default="", max_length=128)
    platform: str = Field(default="", max_length=32)

    _emailValidator = field_validator("email")(_normalizeEmail)


class PasswordResetRequest(BaseModel):
    email: str

    _emailValidator = field_validator("email")(_normalizeEmail)


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    newPassword: str = Field(min_length=10, max_length=256)


class PasswordChangeRequest(BaseModel):
    oldPassword: str = Field(min_length=1, max_length=256)
    newPassword: str = Field(min_length=10, max_length=256)


class IdentityUserOut(BaseModel):
    userId: int
    email: str
    displayName: str
    tier: str
    status: str


class RegisterResponse(BaseModel):
    user: IdentityUserOut


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


class LoginResponse(BaseModel):
    user: IdentityUserOut
    tokens: TokensOut


class RedeemResponse(BaseModel):
    """POST /v1/auth/redeem 成功响应。"""

    mode: str = Field(..., description="invite / trial / recharge")
    user: UserOut
    balance: BalanceOut
    tokens: TokensOut


class RefreshRequest(BaseModel):
    """POST /v1/auth/refresh 请求体。"""

    refreshToken: str = Field(..., description="带 jti 的签名 Refresh JWT")


class LogoutRequest(BaseModel):
    """POST /v1/auth/logout 请求体(可选 body)。"""

    refreshToken: str | None = None


__all__ = [
    "RedeemRequest",
    "RegisterRequest",
    "LoginRequest",
    "PasswordResetRequest",
    "PasswordResetConfirmRequest",
    "PasswordChangeRequest",
    "IdentityUserOut",
    "RegisterResponse",
    "LoginResponse",
    "UserOut",
    "BalanceOut",
    "TokensOut",
    "RedeemResponse",
    "RefreshRequest",
    "LogoutRequest",
]
