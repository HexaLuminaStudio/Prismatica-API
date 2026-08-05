"""Routers 模块聚合导出。"""

from app.routers import account, admin, admin_auth, auth, billing, public

__all__ = [
    "account",
    "admin",
    "admin_auth",
    "auth",
    "billing",
    "public",
]
