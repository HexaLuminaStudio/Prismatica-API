"""ORM 模型聚合导出。

SQLAlchemy 2.x 的 DeclarativeBase 要求所有 Model 在使用前被 import,
否则 `Base.metadata.create_all` 不会扫描到。本模块统一 import 所有表,
任何调用方 `import app.models` 即可触发完整元数据注册。

2026-08-06 重构:
    - 合并:license_models.py(三类 Pydantic 模型)并入 user.py
    - 删除:license_code_seen.py(由 license_code.py 替代)
    - 新增:license_code.py(LicenseCode,issued 立即持久化)
"""

from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.balance_ledger import BalanceLedger
from app.models.bill import Bill
from app.models.code_redemption import CodeRedemption
from app.models.idempotency_key import IdempotencyKey
from app.models.identity import (
    IdentityBalance,
    IdentityDevice,
    PasswordResetToken,
)
from app.models.identity import (
    User as IdentityUser,
)
from app.models.license_code import LicenseCode
from app.models.recharge_record import RechargeRecord
from app.models.refresh_token import RefreshToken
from app.models.revoked_token import RevokedToken
from app.models.stored_refresh_token import StoredRefreshToken
from app.models.subscription import Subscription
from app.models.user import (
    ActivationCode,
    InviteCode,
    RechargeCode,
    TrialCode,
    UserTier,
)
from app.models.user_account import UserAccount, UserBalance, UserDevice

__all__ = [
    "AdminUser",
    "AuditLog",
    "BalanceLedger",
    "Bill",
    "CodeRedemption",
    "IdempotencyKey",
    "IdentityBalance",
    "IdentityDevice",
    "IdentityUser",
    "LicenseCode",
    "PasswordResetToken",
    "RechargeCode",
    "RechargeRecord",
    "RefreshToken",
    "RevokedToken",
    "StoredRefreshToken",
    "Subscription",
    "TrialCode",
    "ActivationCode",
    "InviteCode",
    "UserAccount",
    "UserBalance",
    "UserDevice",
    "UserTier",
]
