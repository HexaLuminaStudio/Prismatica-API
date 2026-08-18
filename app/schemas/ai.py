"""平台 AI 请求与响应模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class AiMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str = Field(..., min_length=1, max_length=200_000)


class AiChatRequest(BaseModel):
    featureCode: Literal["ai_chat", "ai_insight", "ai_report"] = "ai_chat"
    messages: list[AiMessage] = Field(..., min_length=1, max_length=80)
    maxOutputTokens: int | None = Field(default=None, ge=128, le=32768)
    temperature: float = Field(default=0.3, ge=0.0, le=1.5)

    @field_validator("messages")
    @classmethod
    def validateTotalContent(cls, messages: list[AiMessage]) -> list[AiMessage]:
        if sum(len(item.content) for item in messages) > 400_000:
            raise ValueError("消息总长度不能超过 400000 字符")
        return messages


__all__ = ["AiChatRequest", "AiMessage"]
