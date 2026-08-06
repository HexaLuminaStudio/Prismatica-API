"""Services 模块聚合导出(2026-08-06 重构)。

新增 admin_*_service 三件套,与既有 auth / billing / pricing 并列。
"""
from app.services import (
    admin_audit_service,
    admin_auth_service,
    admin_code_service,
    admin_user_service,
    auth_service,
    billing_service,
    pricing,
)

__all__ = [
    "admin_audit_service",
    "admin_auth_service",
    "admin_code_service",
    "admin_user_service",
    "auth_service",
    "billing_service",
    "pricing",
]
