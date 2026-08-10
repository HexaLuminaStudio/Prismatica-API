"""一次性脚本:在指定数据库中创建管理员账号 admin / 123456aaaa。

要求:
- 仅在 admin_users 表中插入唯一一行。
- 密码必须符合 P0-A 策略(>=10 位 + 字母 + 数字),因此后缀追加 4 位字母。
- 使用项目同款 bcrypt cost=12 哈希。
- 通过环境变量注入 DB 连接,避免在源码中硬编码密码。
- 已存在同名且未软删的账号将跳过(失败,退出码 2)。

执行:
    DB_HOST=... DB_PORT=... DB_NAME=... DB_USER=... DB_PASSWORD=... \
        python -m scripts.create_admin_user
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from loguru import logger  # noqa: E402

from app.db import getDb  # noqa: E402
from app.models import AdminUser  # noqa: E402
from app.security.password import hashPassword  # noqa: E402

PLAIN_PASSWORD = "123456aaaa"  # 123456 + 4 位字母,满足项目 P0-A 密码策略
USERNAME = "admin"


def main() -> int:
    with getDb() as db:
        existing = (
            db.query(AdminUser)
            .filter(AdminUser.username == USERNAME, AdminUser.deletedAt.is_(None))
            .one_or_none()
        )
        if existing is not None:
            logger.warning(
                f"[create_admin_user] username={USERNAME} 已存在且未软删,跳过"
            )
            print("RESULT:SKIPPED")
            return 2

        admin = AdminUser(
            userId="adm_" + secrets.token_hex(16),
            username=USERNAME,
            passwordHash=hashPassword(PLAIN_PASSWORD),
            role="admin",
            status="active",
            failedAttempts=0,
        )
        db.add(admin)
        db.commit()
        db.refresh(admin)
        logger.info(
            f"[create_admin_user] 已创建 admin 账号 user_id={admin.userId} role=admin"
        )
        print(
            "RESULT:CREATED\n"
            f"USERNAME: {USERNAME}\n"
            f"PASSWORD: {PLAIN_PASSWORD}\n"
            f"USER_ID: {admin.userId}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
