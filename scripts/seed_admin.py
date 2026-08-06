"""seed_admin(2026-08-05 M2 B1)

启动期种子:
    - 若 `admin_users` 表为空 → 自动创建 `root` 账号
    - 初始密码从环境变量 `ADMIN_BOOTSTRAP_PASSWORD` 读取;若空 → 生成 24 字节随机密码并打印到 stderr(仅启动期一次)
    - 调用前确保 `app/models` 已经被 import(让 SQLAlchemy metadata 注册 admin_users 表)

用法:
    python -m scripts.seed_admin
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

# 让脚本能以 `python -m scripts.seed_admin` 跑通(项目根不在 PYTHONPATH)
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from loguru import logger  # noqa: E402

from app.config import getSettings  # noqa: E402
from app.db import getDb  # noqa: E402
from app.models import AdminUser  # noqa: E402
from app.security.password import hashPassword  # noqa: E402


def _ensureRootAdmin() -> bool:
    """若 admin_users 空表 → 创建 root 账号。返回 True 表示新建。"""
    settings = getSettings()
    with getDb() as db:
        existing = db.query(AdminUser).filter(AdminUser.username == "root").one_or_none()
        if existing is not None:
            logger.info("[seed_admin] root 已存在,跳过种子")
            return False

        bootstrap = (settings.adminBootstrapPassword or "").strip()
        if not bootstrap:
            bootstrap = secrets.token_urlsafe(24)
            logger.warning(
                "[seed_admin] ADMIN_BOOTSTRAP_PASSWORD 未设置,自动生成临时 root 密码(已打印 1 次)"
            )
            # 仅一次性打印到 stderr(供部署脚本捕获),不会进 log 文件
            sys.stderr.write(
                f"\n!!! ROOT ADMIN TEMP PASSWORD (请立即修改并保存) !!!\n"
                f"    username: root\n"
                f"    password: {bootstrap}\n"
                f"!!! 立刻改密码后这条信息失效 !!!\n\n"
            )
            sys.stderr.flush()

        admin = AdminUser(
            userId="adm_" + secrets.token_hex(16),
            username="root",
            passwordHash=hashPassword(bootstrap),
            role="admin",
            status="active",
            failedAttempts=0,
        )
        db.add(admin)
        db.flush()
        logger.info(f"[seed_admin] 已创建 root 账号: user_id={admin.userId}")
        return True


def main() -> int:
    try:
        created = _ensureRootAdmin()
        if created:
            sys.stderr.write("[seed_admin] 完成,root 已创建\n")
        return 0
    except Exception as e:
        logger.exception(f"[seed_admin] 失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
