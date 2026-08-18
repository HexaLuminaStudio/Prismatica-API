"""版本化定价服务。

正式价格优先读取最近发布的数据库版本；测试或尚未迁移的环境使用内置初始目录。
未知功能默认拒绝计费，禁止用一个静默兜底价误收费用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models.pricing import PricingRuleRecord, PricingVersion
from app.schemas.billing import CostPreview, PricingRule, PricingTier

BUILTIN_VERSION = "2026.08.17-affordable-ai"


@dataclass(frozen=True)
class PriceRule:
    featureCode: str
    displayName: str
    billingMode: str
    unitName: str
    unitSize: int = 1
    fixedCost: int = 0
    baseCost: int = 0
    perUnitCost: int = 0
    inputTokenCostPerUnit: int = 0
    outputTokenCostPerUnit: int = 0
    # 仅用于读取旧价格快照；新价格统一使用可配置的每单位 Token 单价。
    inputTokenCostPer1K: int = 0
    outputTokenCostPer1K: int = 0
    tokenPricingVersion: int = 1
    minCost: int = 0
    maxCost: int = 1_000_000
    enabled: bool = True


@dataclass(frozen=True)
class PriceQuote:
    featureCode: str
    displayName: str
    billingMode: str
    unitName: str
    estimatedCost: int
    pricingVersion: str
    ruleSnapshot: dict[str, Any]


BUILTIN_RULES: dict[str, PriceRule] = {
    "analysis_export": PriceRule(
        featureCode="analysis_export",
        displayName="语料分析导出",
        billingMode="fixed",
        unitName="次",
        fixedCost=5,
        minCost=5,
        maxCost=5,
    ),
    "ai_chat": PriceRule(
        featureCode="ai_chat",
        displayName="AI 聊天",
        billingMode="token",
        unitName="Token",
        unitSize=1_000_000,
        inputTokenCostPerUnit=1,
        outputTokenCostPerUnit=2,
        tokenPricingVersion=2,
        minCost=1,
        maxCost=100_000,
    ),
    "ai_insight": PriceRule(
        featureCode="ai_insight",
        displayName="AI 解读",
        billingMode="token",
        unitName="Token",
        unitSize=1_000_000,
        inputTokenCostPerUnit=1,
        outputTokenCostPerUnit=2,
        tokenPricingVersion=2,
        minCost=1,
        maxCost=100_000,
    ),
    "ai_report": PriceRule(
        featureCode="ai_report",
        displayName="AI 研究报告",
        billingMode="token",
        unitName="Token",
        unitSize=1_000_000,
        inputTokenCostPerUnit=1,
        outputTokenCostPerUnit=2,
        tokenPricingVersion=2,
        minCost=1,
        maxCost=100_000,
    ),
    "hsk_download": PriceRule(
        featureCode="hsk_download",
        displayName="HSK 语料下载",
        billingMode="metered",
        unitName="千条",
        unitSize=1_000,
        perUnitCost=3,
        minCost=3,
        maxCost=1_000_000,
    ),
    "global_download": PriceRule(
        featureCode="global_download",
        displayName="全球中介语语料下载",
        billingMode="metered",
        unitName="千条",
        unitSize=1_000,
        perUnitCost=3,
        minCost=3,
        maxCost=1_000_000,
    ),
    "hsk_essay_export": PriceRule(
        featureCode="hsk_essay_export",
        displayName="HSK 作文导出",
        billingMode="metered",
        unitName="百篇",
        unitSize=100,
        perUnitCost=1,
        minCost=1,
        maxCost=1_000_000,
    ),
}

# 旧测试和兼容调用仍可读取这一名称；正式目录只包含已确认需要收费的云端动作。
DEFAULT_RULES: dict[str, PricingRule] = {
    code: PricingRule(
        actionType=code,
        displayName=rule.displayName,
        baseCost=rule.fixedCost or rule.baseCost,
        perUnit=rule.perUnitCost,
        unitName=rule.unitName,
        tiers=[PricingTier(upTo=-1, rate=1.0)],
        minCost=rule.minCost,
        maxCost=rule.maxCost,
        enabled=rule.enabled,
    )
    for code, rule in BUILTIN_RULES.items()
}


def _ceilUnits(value: int, unitSize: int) -> int:
    safeUnitSize = max(1, int(unitSize))
    return max(0, (int(value) + safeUnitSize - 1) // safeUnitSize)


def costFromSnapshot(
    snapshot: dict[str, Any],
    *,
    resourceUsed: int = 0,
    inputTokens: int = 0,
    outputTokens: int = 0,
) -> int:
    """只使用账单快照计算费用，保证调价不影响进行中的任务。"""
    mode = str(snapshot.get("billingMode", ""))
    if mode == "fixed":
        cost = int(snapshot.get("fixedCost", 0) or 0)
    elif mode == "token":
        cost = int(snapshot.get("baseCost", 0) or 0)
        if int(snapshot.get("tokenPricingVersion", 1) or 1) >= 2:
            tokenUnitSize = max(1, int(snapshot.get("unitSize", 1_000_000) or 1_000_000))
            weightedTokenCost = max(0, int(inputTokens)) * int(snapshot.get("inputTokenCostPerUnit", 0) or 0)
            weightedTokenCost += max(0, int(outputTokens)) * int(snapshot.get("outputTokenCostPerUnit", 0) or 0)
            cost += _ceilUnits(weightedTokenCost, tokenUnitSize)
        else:
            # 旧账单仍严格按原快照的“输入/输出分别每千 Token 向上取整”结算。
            cost += _ceilUnits(inputTokens, 1_000) * int(snapshot.get("inputTokenCostPer1K", 0) or 0)
            cost += _ceilUnits(outputTokens, 1_000) * int(snapshot.get("outputTokenCostPer1K", 0) or 0)
    elif mode == "metered":
        cost = int(snapshot.get("baseCost", 0) or 0)
        cost += _ceilUnits(resourceUsed, int(snapshot.get("unitSize", 1) or 1)) * int(
            snapshot.get("perUnitCost", 0) or 0
        )
    else:
        raise ApiError("PRICING_RULE_INVALID", "账单价格快照无效", httpStatus=409)
    minimum = int(snapshot.get("minCost", 0) or 0)
    maximum = int(snapshot.get("maxCost", 1_000_000) or 1_000_000)
    return max(minimum, min(maximum, cost))


class PricingService:
    """版本化定价门面。"""

    def _publishedVersion(self, db: Session) -> PricingVersion | None:
        connection = db.connection()
        if not inspect(connection).has_table("pricing_versions"):
            return None
        try:
            return db.execute(
                select(PricingVersion)
                .where(PricingVersion.status == "published")
                .order_by(PricingVersion.publishedAt.desc(), PricingVersion.versionId.desc())
                .limit(1)
            ).scalar_one_or_none()
        except (OperationalError, ProgrammingError):
            return None

    def _recordToRule(self, record: PricingRuleRecord) -> PriceRule:
        ruleMeta = dict(record.ruleMeta or {})
        usesAffordableTokenPricing = (
            record.billingMode == "token" and int(ruleMeta.get("tokenPricingVersion", 0) or 0) >= 2
        )
        return PriceRule(
            featureCode=record.featureCode,
            displayName=record.displayName,
            billingMode=record.billingMode,
            unitName=record.unitName,
            unitSize=int(record.unitSize or 1),
            fixedCost=int(record.fixedCost or 0),
            baseCost=int(record.baseCost or 0),
            perUnitCost=int(record.perUnitCost or 0),
            inputTokenCostPerUnit=(int(record.inputTokenCostPer1K or 0) if usesAffordableTokenPricing else 0),
            outputTokenCostPerUnit=(int(record.outputTokenCostPer1K or 0) if usesAffordableTokenPricing else 0),
            inputTokenCostPer1K=(0 if usesAffordableTokenPricing else int(record.inputTokenCostPer1K or 0)),
            outputTokenCostPer1K=(0 if usesAffordableTokenPricing else int(record.outputTokenCostPer1K or 0)),
            tokenPricingVersion=2 if usesAffordableTokenPricing else 1,
            minCost=int(record.minCost or 0),
            maxCost=int(record.maxCost or 1_000_000),
            enabled=bool(record.enabled),
        )

    def catalogStatus(
        self,
        db: Session | None = None,
    ) -> tuple[str, list[PriceRule], str, datetime | None]:
        """返回当前实际参与报价的目录、来源与生效时间。"""
        if db is not None:
            version = self._publishedVersion(db)
            if version is not None:
                records = (
                    db.execute(
                        select(PricingRuleRecord)
                        .where(PricingRuleRecord.versionId == version.versionId)
                        .order_by(PricingRuleRecord.ruleId.asc())
                    )
                    .scalars()
                    .all()
                )
                return (
                    version.versionCode,
                    [self._recordToRule(record) for record in records],
                    "published",
                    version.publishedAt,
                )
        return BUILTIN_VERSION, list(BUILTIN_RULES.values()), "builtin", None

    def catalog(self, db: Session | None = None) -> tuple[str, list[PriceRule]]:
        version, rules, _source, _effectiveAt = self.catalogStatus(db)
        return version, rules

    def rule(self, actionType: str, db: Session | None = None) -> PriceRule:
        _version, rules = self.catalog(db)
        rule = next((item for item in rules if item.featureCode == actionType), None)
        if rule is None or not rule.enabled:
            raise ApiError("PRICING_RULE_NOT_FOUND", f"功能 {actionType} 尚未配置可用价格", httpStatus=409)
        return rule

    def quote(
        self,
        actionType: str,
        *,
        db: Session | None = None,
        resourceUsed: int = 0,
        inputTokens: int = 0,
        outputTokens: int = 0,
    ) -> PriceQuote:
        version, _rules = self.catalog(db)
        rule = self.rule(actionType, db)
        snapshot = asdict(rule)
        cost = costFromSnapshot(
            snapshot,
            resourceUsed=resourceUsed,
            inputTokens=inputTokens,
            outputTokens=outputTokens,
        )
        return PriceQuote(
            featureCode=rule.featureCode,
            displayName=rule.displayName,
            billingMode=rule.billingMode,
            unitName=rule.unitName,
            estimatedCost=cost,
            pricingVersion=version,
            ruleSnapshot=snapshot,
        )

    def estimate(self, actionType: str, resourceUsed: int, db: Session | None = None) -> int:
        return self.quote(actionType, db=db, resourceUsed=resourceUsed).estimatedCost

    def preview(
        self,
        actionType: str,
        resourceUsed: int,
        currentBalance: int,
        db: Session | None = None,
        *,
        inputTokens: int = 0,
        outputTokens: int = 0,
    ) -> CostPreview:
        quote = self.quote(
            actionType,
            db=db,
            resourceUsed=resourceUsed,
            inputTokens=inputTokens,
            outputTokens=outputTokens,
        )
        return CostPreview(
            actionType=actionType,
            displayName=quote.displayName,
            resourceUsed=resourceUsed,
            unitName=quote.unitName,
            estimatedCost=quote.estimatedCost,
            currentBalance=currentBalance,
            balanceAfter=max(0, currentBalance - quote.estimatedCost),
            affordable=currentBalance >= quote.estimatedCost,
            tierBreakdown=[],
            pricingVersion=quote.pricingVersion,
            billingMode=quote.billingMode,
            ruleSnapshot=quote.ruleSnapshot,
        )

    def publicCatalog(self, db: Session | None = None) -> dict[str, Any]:
        version, rules, source, effectiveAt = self.catalogStatus(db)
        return {
            "version": version,
            "state": "active",
            "source": source,
            "effectiveAt": effectiveAt.isoformat() if effectiveAt is not None else None,
            "refreshAfterSeconds": 30,
            "rules": [asdict(rule) for rule in rules if rule.enabled],
        }


_pricingSingleton: PricingService | None = None


def getPricingService() -> PricingService:
    global _pricingSingleton
    if _pricingSingleton is None:
        _pricingSingleton = PricingService()
    return _pricingSingleton


__all__ = [
    "BUILTIN_RULES",
    "BUILTIN_VERSION",
    "DEFAULT_RULES",
    "PriceQuote",
    "PriceRule",
    "PricingService",
    "costFromSnapshot",
    "getPricingService",
]
