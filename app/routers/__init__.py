"""Routers 模块聚合导出(2026-08-06 重构)。

admin / admin_auth 拆分 → admin_auth / users / codes / audit / metrics
"""
from app.routers import account, admin_audit, admin_auth, admin_codes, admin_metrics, admin_users, auth, billing, public

__all__ = [
    "account",
    "admin_audit",
    "admin_auth",
    "admin_codes",
    "admin_metrics",
    "admin_users",
    "auth",
    "billing",
    "public",
]
