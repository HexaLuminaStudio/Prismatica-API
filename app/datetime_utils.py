"""后端统一时间语义。

数据库 ``DATETIME(3)`` 字段统一保存 naive UTC；API 输出统一带 ``Z``，
避免浏览器把无时区的 UTC 字符串误当成本地时间。
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcNowNaive() -> datetime:
    """返回供 DATETIME 字段写入的 naive UTC 当前时间。"""
    return datetime.now(UTC).replace(tzinfo=None)


def asUtc(value: datetime | None) -> datetime | None:
    """把数据库 datetime 转为带 UTC 时区的响应值。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def toUtcIso(value: datetime | None) -> str:
    """输出明确带 ``Z`` 的 ISO-8601 UTC 时间。"""
    awareValue = asUtc(value)
    if awareValue is None:
        return ""
    return awareValue.isoformat().replace("+00:00", "Z")


def parseUtcIso(value: str) -> datetime:
    """解析 ISO-8601 时间并归一化为供数据库比较的 naive UTC。"""
    parsedValue = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsedValue.tzinfo is None:
        return parsedValue
    return parsedValue.astimezone(UTC).replace(tzinfo=None)


__all__ = ["asUtc", "parseUtcIso", "toUtcIso", "utcNowNaive"]
