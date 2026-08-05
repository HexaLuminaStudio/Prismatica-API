"""管理后台认证 Pydantic 模型(2026-08-05 M2 B1)。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    """POST /admin/login 请求体。"""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class AdminMeResponse(BaseModel):
    """GET /admin/me 响应。"""

    userId: str
    username: str
    role: str
    status: str
    lastLoginAt: datetime | None = None


class AdminChangePasswordRequest(BaseModel):
    """POST /admin/me/change-password 请求体。"""

    oldPassword: str = Field(..., min_length=1)
    newPassword: str = Field(..., min_length=8, max_length=256)


__all__ = [
    "AdminLoginRequest",
    "AdminMeResponse",
    "AdminChangePasswordRequest",
]
