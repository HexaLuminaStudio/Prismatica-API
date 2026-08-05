# coding: utf-8
"""智慧计价服务。

对齐客户端 `app/core/services/pricing_service.py` 的 DEFAULT_RULES:
    基础费 + (资源量 / 单价单位) * perUnit * 阶梯倍率
    最终 clamp 在 [minCost, maxCost]
"""
from __future__ import annotations

from typing import Optional

from app.schemas.billing import CostPreview, PricingRule, PricingTier


# 默认规则(与客户端 DEFAULT_RULES 对齐)
DEFAULT_RULES: dict[str, PricingRule] = {
    "freq_analyze": PricingRule(
        actionType="freq_analyze",
        displayName="词频分析",
        baseCost=5,
        perUnit=2,
        unitName="千字",
        tiers=[
            PricingTier(upTo=10, rate=1.0),
            PricingTier(upTo=50, rate=0.8),
            PricingTier(upTo=-1, rate=0.5),
        ],
        minCost=5,
        maxCost=200,
    ),
    "kwic_search": PricingRule(
        actionType="kwic_search",
        displayName="KWIC 检索",
        baseCost=1,
        perUnit=1,
        unitName="千字",
        minCost=1,
        maxCost=50,
    ),
    "co_occurrence": PricingRule(
        actionType="co_occurrence",
        displayName="共现分析",
        baseCost=3,
        perUnit=2,
        unitName="千字",
        minCost=3,
        maxCost=100,
    ),
    "dependency_parse": PricingRule(
        actionType="dependency_parse",
        displayName="句法依存",
        baseCost=10,
        perUnit=5,
        unitName="千字",
        tiers=[
            PricingTier(upTo=5, rate=1.0),
            PricingTier(upTo=-1, rate=0.7),
        ],
        minCost=10,
        maxCost=300,
    ),
    "word_cloud": PricingRule(
        actionType="word_cloud",
        displayName="词云生成",
        baseCost=2,
        perUnit=1,
        unitName="千字",
        minCost=2,
        maxCost=50,
    ),
    "sentiment": PricingRule(
        actionType="sentiment",
        displayName="情感分析",
        baseCost=5,
        perUnit=3,
        unitName="千字",
        minCost=5,
        maxCost=150,
    ),
    "bias_stats": PricingRule(
        actionType="bias_stats",
        displayName="偏误统计",
        baseCost=8,
        perUnit=3,
        unitName="千字",
        minCost=8,
        maxCost=200,
    ),
    "corpus_import": PricingRule(
        actionType="corpus_import",
        displayName="语料导入",
        baseCost=3,
        perUnit=1,
        unitName="千字",
        minCost=3,
        maxCost=80,
    ),
    "corpus_download": PricingRule(
        actionType="corpus_download",
        displayName="语料下载",
        baseCost=2,
        perUnit=1,
        unitName="千字",
        minCost=2,
        maxCost=60,
    ),
}


class PricingService:
    """计价门面(无状态)。"""

    def rule(self, actionType: str) -> PricingRule:
        rule = DEFAULT_RULES.get(actionType)
        if rule is None:
            # 未知动作返回「按次计费」兜底规则
            return PricingRule(
                actionType=actionType,
                displayName=actionType,
                baseCost=1,
                perUnit=1,
                unitName="次",
                minCost=1,
                maxCost=100,
            )
        return rule

    def estimate(self, actionType: str, resourceUsed: int) -> int:
        """估算费用(整数币)。"""
        rule = self.rule(actionType)
        units = (resourceUsed + 999) // 1000  # 千字向上取整
        # 阶梯倍率
        rate = 1.0
        for tier in rule.tiers:
            if tier.upTo == -1 or units <= tier.upTo:
                rate = tier.rate
                break
        cost = rule.baseCost + int(units * rule.perUnit * rate)
        return max(rule.minCost, min(rule.maxCost, cost))

    def preview(
        self,
        actionType: str,
        resourceUsed: int,
        currentBalance: int,
    ) -> CostPreview:
        rule = self.rule(actionType)
        cost = self.estimate(actionType, resourceUsed)
        return CostPreview(
            actionType=actionType,
            displayName=rule.displayName,
            resourceUsed=resourceUsed,
            unitName=rule.unitName,
            estimatedCost=cost,
            currentBalance=currentBalance,
            balanceAfter=max(0, currentBalance - cost),
            affordable=currentBalance >= cost,
            tierBreakdown=[{"upTo": t.upTo, "rate": t.rate} for t in rule.tiers],
        )


_pricingSingleton: Optional[PricingService] = None


def getPricingService() -> PricingService:
    """全局单例。"""
    global _pricingSingleton
    if _pricingSingleton is None:
        _pricingSingleton = PricingService()
    return _pricingSingleton


__all__ = ["DEFAULT_RULES", "PricingService", "getPricingService"]