"""旧 UUID 兑换登录模型(过渡期保留)。

M3 后真实用户身份由 app.models.identity.User / IdentityDevice / IdentityBalance
承载,字段是 BIGINT user_id,完全符合 schema.sql 设计。旧的 UserAccount /
UserDevice / UserBalance 仍被 auth_service.py 引用,这里把它们 alias 到
IdentityUser / IdentityDevice / IdentityBalance,以避免:
    - SQLAlchemy 2.x 重复注册同表
    - 旧代码用 CHAR(36) 字段做查询时炸(BIGINT PK 不允许 CHAR 字符串)

旧代码 auth_service.py 在 M6 兑换码升级后会逐步下线,届时直接删除本模块。
"""
from __future__ import annotations

from app.models.identity import IdentityBalance, IdentityDevice, User as IdentityUser


UserAccount = IdentityUser
UserDevice = IdentityDevice
UserBalance = IdentityBalance


__all__ = ["UserAccount", "UserDevice", "UserBalance"]
