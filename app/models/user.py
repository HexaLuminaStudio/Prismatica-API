"""凭证码 Pydantic 模型(2026-08-06 从 license_models.py 迁移而来)。

承载三类 INV/TRY/RCH 凭证的载荷定义,与客户端
`app/core/models/auth_models.py` 的字段命名保持一致,
以便 `signed_code.tryParseAnyCode()` 直接可用、后端验签后可强类型化。

不与 SQLAlchemy ORM 混在一起,保持纯数据类形态。
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class UserTier(StrEnum):
    """用户档位枚举。"""

    FREE = "free"
    PRO = "pro"
    TEAM = "team"
    GUEST = "guest"
    TRIAL = "trial"
    BETA = "beta"
    BETA_PRO = "beta_pro"
    PAID = "paid"


class InviteCode(BaseModel):
    """邀请码载荷。"""

    code: str = Field(..., description="INV-XXXX-XXXX-XXXX-XXXX")
    maxUses: int = 1
    grantedBalance: int = 100
    grantedDays: int = 30
    tier: UserTier = UserTier.BETA
    expireAt: datetime
    issuedAt: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1


class TrialCode(BaseModel):
    """体验码载荷。"""

    code: str
    maxUses: int = 1
    grantedBalance: int = 20
    grantedDays: int = 7
    tier: UserTier = UserTier.TRIAL
    expireAt: datetime
    issuedAt: datetime = Field(default_factory=datetime.utcnow)
    version: int = 1


class RechargeCode(BaseModel):
    """充值码载荷。"""

    code: str
    amount: int = Field(..., ge=1)
    expireAt: datetime
    issuedAt: datetime = Field(default_factory=datetime.utcnow)
    note: str = ""
    version: int = 1


class ActivationCode(BaseModel):
    """存量激活码载荷(legacy 格式,无 code 字段)。

    格式:base64(JSON({deviceCode, validityPeriod, userType, issuedAt, extras?, signature}))
    """

    deviceCode: str = ""
    validityPeriod: str | None = None
    userType: str = "正式用户"
    issuedAt: datetime = Field(default_factory=datetime.utcnow)
    extras: dict | None = None
    version: int = 1


__all__ = [
    "UserTier",
    "InviteCode",
    "TrialCode",
    "RechargeCode",
    "ActivationCode",
]
