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


def testAiTokenPrice_UsesSeparateInputAndOutputThousands() -> None:
    pricing = getPricingService()
    quote = pricing.quote("ai_chat", inputTokens=1_001, outputTokens=2_001)
    assert quote.billingMode == "token"
    assert quote.estimatedCost == 2 * 1 + 3 * 2


def testUnknownOrLocalAction_IsNeverSilentlyCharged() -> None:
    pricing = getPricingService()
    for actionType in ("totally_unknown", "freq_analyze", "kwic_search"):
        with pytest.raises(ApiError) as exc:
            pricing.estimate(actionType, 10_000)
        assert exc.value.code == "PRICING_RULE_NOT_FOUND"


def testPublicCatalog_AdvertisesThirtySecondRefresh() -> None:
    catalog = getPricingService().publicCatalog()
    assert catalog["refreshAfterSeconds"] == 30
    assert {rule["featureCode"] for rule in catalog["rules"]} >= {
        "analysis_export",
        "ai_chat",
        "ai_insight",
        "ai_report",
    }
