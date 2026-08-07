"""限流存储故障时的可用性保护。"""

from __future__ import annotations


def test_rate_limiter_has_availability_fallback():
    """Redis 故障时不能把受限流保护的接口拖成 500。"""
    from app.main import limiter

    assert limiter._in_memory_fallback_enabled is True
    assert limiter._swallow_errors is True
