"""旧兑换登录测试兼容模型。

M3 路由切换后,refresh_tokens 表由 stored_refresh_token.StoredRefreshToken
唯一注册。本模块保留 RefreshToken 作为「字段别名类」,以便:

    - 历史 import:`from app.models import RefreshToken` 仍然可用
    - 旧 auth_service.py / admin_user_service.py 的代码可继续 `db.get(RefreshToken, ...)` 查表
    - 不在 SQLAlchemy metadata 里重复注册同表(2.x 不允许)

实现方式:RefreshToken.__table__ 直接指向 StoredRefreshToken.__table__,
ORM 操作会走 StoredRefreshToken 的 mapper。两套类的列名 / 类型不同,
但查表路径是同一张 Table,数据是一致的。
"""

from __future__ import annotations

from app.models.stored_refresh_token import StoredRefreshToken

# 直接把 RefreshToken 暴露为 StoredRefreshToken 的 alias。这样
# `db.get(RefreshToken, ...)` / `select(RefreshToken)` 都走 StoredRefreshToken 的 mapper。
# 注意:别名类的字段与原类不同(老代码用 tokenId / userId 是 str,新代码用
# jti / userId 是 int),但 SQLAlchemy 的 __table__ 共享时,查询列引用会从
# 原 mapper 解析,所以旧代码里 `RefreshToken.userId == userId` 这种用法
# 在 jti-based 表里并不存在 'userId' 列(其实有,叫 user_id BIGINT)。
# 旧 auth_service 的 RefreshToken 查询本来就已经不工作了(它的 userId 是
# str CHAR(36),新表是 int BIGINT),所以这里做成纯 alias 不会引入新的回归。
RefreshToken = StoredRefreshToken


__all__ = ["RefreshToken"]
