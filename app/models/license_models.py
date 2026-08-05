# coding: utf-8
"""三类凭证的 Pydantic 模型。

与客户端 `app/core/models/auth_models.py` 完全兼容(JSON 序列化/反序列化字段一致),
这样 `signed_code.tryParseAnyCode()` 直接可用,后端验签后即可强类型化。
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class UserTier(str, Enum):
    """用户档位。"""

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


__all__ = ["UserTier", "InviteCode", "TrialCode", "RechargeCode"]