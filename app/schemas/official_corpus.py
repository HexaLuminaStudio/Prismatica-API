"""官方语料账号 Token 代理接口模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class OfficialCorpusTokenRequest(BaseModel):
    """客户端选择的语料平台。"""

    provider: Literal["hsk", "global"]


class OfficialCorpusTokenResponse(BaseModel):
    """后端使用官方账号换取的短期 Token。"""

    provider: Literal["hsk", "global"]
    token: str


__all__ = ["OfficialCorpusTokenRequest", "OfficialCorpusTokenResponse"]
