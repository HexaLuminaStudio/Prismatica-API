"""受保护资源下载接口响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ResourceManifestOut(BaseModel):
    """单个客户端资源的短期下载清单。"""

    resourceKey: str
    displayName: str
    fileName: str
    version: str
    sha256: str
    downloadUrl: str
    expiresAt: datetime


class ResourceBootstrapResponse(BaseModel):
    """当前登录账号可下载的完整资源清单。"""

    resources: list[ResourceManifestOut] = Field(default_factory=list)


__all__ = ["ResourceBootstrapResponse", "ResourceManifestOut"]
