"""admin_users 表结构迁移(2026-08-06 M3)。

为已有部署补加 2 个字段:
    - deleted_at    DATETIME(3) NULL
    - pwd_reset_at  DATETIME(3) NULL
    - 索引 idx_admin_users_deleted_at

适用:
    - admin_users 已存在的生产实例(对应 schema.sql 旧版)
    - 不会修改/删除任何已有数据
    - 重复执行安全(IF NOT EXISTS / 错误码吞掉)

用法(在项目根):
    python -m scripts.migrate_add_admin_user_fields
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from loguru import logger  # noqa: E402

from app.db import engine  # noqa: E402

# ALTER 语句必须顺序执行(MySQL 单条 ALTER 支持多动作,但分开更易读 + 错误定位)
ALTERS = [
    # deleted_at
    (
        "ALTER TABLE admin_users "
        "ADD COLUMN deleted_at DATETIME(3) NULL "
        "COMMENT '软删除时间戳;非空即已删除,username 永久占用'"
    ),
    # pwd_reset_at
    (
        "ALTER TABLE admin_users "
        "ADD COLUMN pwd_reset_at DATETIME(3) NULL "
        "COMMENT '密码重置时间戳;cookie 颁发时间早于此值即失效'"
    ),
    # 索引(IF NOT EXISTS 在 MySQL 8.0.29+ 才支持;此处用错误码 1061 兼容老版本)
    (
        "ALTER TABLE admin_users "
        "ADD INDEX idx_admin_users_deleted_at (deleted_at)"
    ),
]


def _isDuplicateColumn(err: Exception) -> bool:
    """MySQL 1060 = Duplicate column name;1061 = Duplicate key name。视为幂等成功。"""
    msg = str(err)
    return ("1060" in msg and "Duplicate column" in msg) or (
        "1061" in msg and "Duplicate key" in msg
    )


def main() -> int:
    from sqlalchemy import text

    failed = 0
    with engine.begin() as conn:
        for sql in ALTERS:
            try:
                conn.execute(text(sql))
                logger.info(f"[migrate] OK: {sql[:60]}...")
            except Exception as e:  # noqa: BLE001
                if _isDuplicateColumn(e):
                    errBrief = str(e)[:80]
                    logger.info(
                        f"[migrate] SKIP(已存在): {sql[:60]}... ({errBrief})"
                    )
                    continue
                logger.exception(f"[migrate] FAILED: {sql}")
                failed += 1

    if failed:
        logger.error(f"[migrate] {failed} 条 ALTER 失败,请手动检查")
        return 1
    logger.info("[migrate] admin_users 表结构迁移完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
