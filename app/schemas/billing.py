"""计费 Pydantic 模型 — 对齐 PRD §5.4。"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------
# 计价规则(与客户端 pricing_service.DEFAULT_RULES 对齐)
# ---------------------------------------------------------------------


class PricingTier(BaseModel):
    """资源阶梯。"""

    upTo: int = Field(..., description="阶梯上限(-1 表示无穷大)")
    rate: float = Field(..., description="相对 perUnit 的倍率")


class PricingRule(BaseModel):
    """单动作计价规则。"""

    actionType: str
    displayName: str
    baseCost: int = Field(..., ge=0)
    perUnit: int = Field(..., ge=0, description="单价(币 / 千字 或 币 / 次)")
    unitName: str
    tiers: list[PricingTier] = Field(default_factory=list)
    minCost: int = 0
    maxCost: int = 10000
    enabled: bool = True


# ---------------------------------------------------------------------
# Estimate
# ---------------------------------------------------------------------


class EstimateRequest(BaseModel):
    """POST /v1/billing/estimate 请求体。"""

    actionType: str = Field(..., description="freq_analyze / kwic_search / ...")
    resourceUsed: int = Field(..., ge=0)


class CostPreview(BaseModel):
    """与客户端 CostPreview 完全一致。"""

    actionType: str
    displayName: str
    resourceUsed: int
    unitName: str
    estimatedCost: int
    currentBalance: int
    balanceAfter: int
    affordable: bool
    tierBreakdown: list[dict] = Field(default_factory=list)
    pricingVersion: str = ""
    billingMode: str = ""
    ruleSnapshot: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------
# Preauth
# ---------------------------------------------------------------------


class PreauthRequest(BaseModel):
    """POST /v1/billing/preauth 请求体。"""

    actionType: str
    resourceUsed: int = Field(..., ge=0)
    taskId: str = Field(default="")
    description: str = Field(default="")


class PreauthResponse(BaseModel):
    """POST /v1/billing/preauth 响应。"""

    billId: str
    estimatedCost: int
    balanceAfter: int
    pricingVersion: str = ""
    billingMode: str = ""


# ---------------------------------------------------------------------
# Settle
# ---------------------------------------------------------------------


class SettleRequest(BaseModel):
    """POST /v1/billing/settle 请求体。"""

    billId: str
    realCost: int = Field(..., ge=0)
    resourceUsed: int = Field(default=0, ge=0)


class SettleResponse(BaseModel):
    """POST /v1/billing/settle 响应。"""

    billId: str
    realCost: int
    balanceAfter: int
    refunded: int = Field(default=0, description="结算时返还的预占金额")


# ---------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------


class RefundRequest(BaseModel):
    """POST /v1/billing/refund 请求体。"""

    billId: str


class RefundResponse(BaseModel):
    """POST /v1/billing/refund 响应。"""

    billId: str
    refundedAmount: int
    balanceAfter: int


__all__ = [
    "PricingTier",
    "PricingRule",
    "EstimateRequest",
    "CostPreview",
    "PreauthRequest",
    "PreauthResponse",
    "SettleRequest",
    "SettleResponse",
    "RefundRequest",
    "RefundResponse",
]
