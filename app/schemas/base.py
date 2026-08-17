"""Pydantic 响应模型公共基类。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_serializer

from app.datetime_utils import toUtcIso


class UtcResponseModel(BaseModel):
    """所有 datetime 在 JSON 中序列化为明确的 UTC 时间。"""

    @field_serializer("*", when_used="json", check_fields=False)
    def _serializeUtcFields(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return toUtcIso(value)
        return value


__all__ = ["UtcResponseModel"]
