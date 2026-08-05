# coding: utf-8
"""ORM 模型聚合导出。

SQLAlchemy 2.x 的 DeclarativeBase 要求所有 Model 在使用前被 import,
否则 `Base.metadata.create_all` 不会扫描到。本模块统一 import 所有表,
任何调用方 `import app.models` 即可触发完整元数据注册。
"""
from app.models.audit_log import AuditLog
from app.models.bill import Bill
from app.models.license_code_seen import LicenseCodeSeen
from app.models.recharge_record import RechargeRecord
from app.models.refresh_token import RefreshToken
from app.models.user_account import UserAccount, UserBalance, UserDevice

__all__ = [
    "AuditLog",
    "Bill",
    "LicenseCodeSeen",
    "RechargeRecord",
    "RefreshToken",
    "UserAccount",
    "UserBalance",
    "UserDevice",
]