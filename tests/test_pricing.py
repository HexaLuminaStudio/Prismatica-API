"""动态定价服务的核心业务规则。"""
from __future__ import annotations

import pytest

from app.errors import ApiError
from app.services.pricing import getPricingService


def testAnalysisExport_IsFixedRegardlessOfVolume() -> None:
    pricing = getPricingService()
    assert pricing.estimate("analysis_export", 0) == 5
    assert pricing.estimate("analysis_export", 1) == 5
    assert pricing.estimate("analysis_export", 10_000_000) == 5


def testAnalysisExport_PreviewShowsAffordabilityAndVersion() -> None:
    pricing = getPricingService()
    preview = pricing.preview("analysis_export", 99_999, currentBalance=100)
    assert preview.estimatedCost == 5
    assert preview.affordable is True
    assert preview.balanceAfter == 95
    assert preview.billingMode == "fixed"
    assert preview.pricingVersion

    poor = pricing.preview("analysis_export", 1, currentBalance=4)
    assert poor.affordable is False


def testAiTokenPrice_UsesWeightedMillionTokenUnitAndRoundsOnce() -> None:
    pricing = getPricingService()
    quote = pricing.quote("ai_chat", inputTokens=1_001, outputTokens=2_001)
    assert quote.billingMode == "token"
    assert quote.ruleSnapshot["unitSize"] == 1_000_000
    assert quote.ruleSnapshot["tokenPricingVersion"] == 2
    assert quote.estimatedCost == 1

    larger = pricing.quote("ai_chat", inputTokens=1_000_000, outputTokens=1_000_000)
    assert larger.estimatedCost == 3


def testAiTokenPrice_PreservesLegacySnapshotFormula() -> None:
    from app.services.pricing import costFromSnapshot

    legacySnapshot = {
        "billingMode": "token",
        "baseCost": 0,
        "inputTokenCostPer1K": 1,
        "outputTokenCostPer1K": 2,
        "minCost": 1,
        "maxCost": 100_000,
    }
    assert costFromSnapshot(legacySnapshot, inputTokens=1_001, outputTokens=2_001) == 8


def testUnknownOrLocalAction_IsNeverSilentlyCharged() -> None:
    pricing = getPricingService()
    for actionType in ("totally_unknown", "freq_analyze", "kwic_search"):
        with pytest.raises(ApiError) as exc:
            pricing.estimate(actionType, 10_000)
        assert exc.value.code == "PRICING_RULE_NOT_FOUND"


def testPublicCatalog_AdvertisesThirtySecondRefresh() -> None:
    catalog = getPricingService().publicCatalog()
    assert catalog["refreshAfterSeconds"] == 30
    assert catalog["state"] == "active"
    assert catalog["source"] == "builtin"
    assert catalog["effectiveAt"] is None
    assert {rule["featureCode"] for rule in catalog["rules"]} >= {
        "analysis_export",
        "ai_chat",
        "ai_insight",
        "ai_report",
        "hsk_download",
        "global_download",
        "hsk_essay_export",
    }


@pytest.mark.parametrize("featureCode", ["hsk_download", "global_download"])
def testCorpusDownload_RoundsUpEveryThousandRecords(featureCode: str) -> None:
    pricing = getPricingService()
    assert pricing.estimate(featureCode, 1) == 3
    assert pricing.estimate(featureCode, 999) == 3
    assert pricing.estimate(featureCode, 1_000) == 3
    assert pricing.estimate(featureCode, 1_001) == 6
    assert pricing.estimate(featureCode, 2_000) == 6


def testHskEssayExport_RoundsUpEveryHundredEssays() -> None:
    pricing = getPricingService()
    assert pricing.estimate("hsk_essay_export", 1) == 1
    assert pricing.estimate("hsk_essay_export", 99) == 1
    assert pricing.estimate("hsk_essay_export", 100) == 1
    assert pricing.estimate("hsk_essay_export", 101) == 2
    assert pricing.estimate("hsk_essay_export", 200) == 2
