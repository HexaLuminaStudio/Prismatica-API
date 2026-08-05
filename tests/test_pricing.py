"""定价服务单元测试(纯逻辑,无 DB 依赖)。"""
from __future__ import annotations

from app.services.pricing import getPricingService


def test_estimate_freq_analyze_min_cost():
    ps = getPricingService()
    # 0 字 → 千字=0 → baseCost=5,clamp 到 minCost=5
    assert ps.estimate("freq_analyze", 0) == 5


def test_estimate_freq_analyze_one_thousand():
    ps = getPricingService()
    # 1000 字 → 1 千字 → 5 + 1*2*1.0 = 7
    assert ps.estimate("freq_analyze", 1000) == 7


def test_estimate_freq_analyze_high_volume_discount():
    ps = getPricingService()
    # 60 千字 → 命中 upTo=-1 阶梯(0.5 倍率):5 + 60*2*0.5 = 65
    assert ps.estimate("freq_analyze", 60_000) == 65


def test_estimate_clamped_to_max():
    ps = getPricingService()
    # 200 千字 → 5 + 200*2*0.5 = 205 → clamp 到 maxCost=200
    assert ps.estimate("freq_analyze", 200_000) == 200


def test_preview_affordability():
    ps = getPricingService()
    preview = ps.preview("freq_analyze", 1000, currentBalance=100)
    assert preview.estimatedCost == 7
    assert preview.affordable is True
    assert preview.balanceAfter == 93

    poor = ps.preview("freq_analyze", 1000, currentBalance=3)
    assert poor.affordable is False


def test_unknown_action_falls_back():
    ps = getPricingService()
    rule = ps.rule("totally_unknown")
    assert rule.baseCost >= 1
    assert ps.estimate("totally_unknown", 0) >= 1
