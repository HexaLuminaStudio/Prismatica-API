"""通用错误 envelope + Pydantic 模型。

与 PRD §5.1 一致:
    {"error": {"code": "...", "message": "...", "requestId": "...", "details": {...}}}
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApiErrorBody(BaseModel):
    """错误 envelope 的 error 字段。"""

    code: str
    message: str
    requestId: str | None = None
    details: dict[str, Any] | None = None


class ApiErrorEnvelope(BaseModel):
    """错误 envelope(序列化为 {"error": {...}})。"""

    error: ApiErrorBody


__all__ = ["ApiErrorBody", "ApiErrorEnvelope"]
