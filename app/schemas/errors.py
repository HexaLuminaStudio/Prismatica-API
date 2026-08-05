# coding: utf-8
"""通用错误 envelope + Pydantic 模型。

与 PRD §5.1 一致:
    {"error": {"code": "...", "message": "...", "requestId": "...", "details": {...}}}
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ApiErrorBody(BaseModel):
    """错误 envelope 的 error 字段。"""

    code: str
    message: str
    requestId: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class ApiErrorEnvelope(BaseModel):
    """错误 envelope(序列化为 {"error": {...}})。"""

    error: ApiErrorBody


__all__ = ["ApiErrorBody", "ApiErrorEnvelope"]