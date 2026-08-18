"""执行用户认证版本号迁移。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from scripts import migrate_account_billing as migration

MIGRATION_ID = "2026_08_17_auth_version"
migration.MIGRATION_ID = MIGRATION_ID
migration.MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / f"{MIGRATION_ID}.sql"

_baseMigrateUp = migration.migrateUp


def migrateUpAuthVersion(connection: Connection, statements: list[str]) -> bool:
    """兼容全新数据库已经由基础迁移创建 auth_version 的情况。"""
    inspector = inspect(connection)
    if not inspector.has_table("users"):
        raise RuntimeError("缺少 users 表，请先执行账号计费基础迁移")
    existingColumns = {column["name"] for column in inspector.get_columns("users")}
    filteredStatements = [
        statement
        for statement in statements
        if not (statement.startswith("ALTER TABLE users ADD COLUMN auth_version") and "auth_version" in existingColumns)
    ]
    return _baseMigrateUp(connection, filteredStatements)


migration.migrateUp = migrateUpAuthVersion


if __name__ == "__main__":
    raise SystemExit(migration.main())
