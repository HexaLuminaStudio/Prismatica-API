"""执行 2026-08-10 版本化定价迁移。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from scripts import migrate_account_billing as migration

MIGRATION_ID = "2026_08_10_dynamic_pricing"
migration.MIGRATION_ID = MIGRATION_ID
migration.MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / f"{MIGRATION_ID}.sql"

_baseMigrateUp = migration.migrateUp


def migrateUpDynamic(connection: Connection, statements: list[str]) -> bool:
    """跳过中断前已成功添加的 bills 列，使 MySQL DDL 可安全重跑。"""
    inspector = inspect(connection)
    if not inspector.has_table("bills"):
        raise RuntimeError("缺少 bills 表，请先执行账号计费基础迁移")
    existingColumns = {column["name"] for column in inspector.get_columns("bills")}
    filteredStatements: list[str] = []
    columnMarkers = {
        "ALTER TABLE bills ADD COLUMN pricing_version": "pricing_version",
        "ALTER TABLE bills ADD COLUMN pricing_snapshot": "pricing_snapshot",
        "ALTER TABLE bills ADD COLUMN input_tokens": "input_tokens",
        "ALTER TABLE bills ADD COLUMN output_tokens": "output_tokens",
    }
    for statement in statements:
        marker = next((prefix for prefix in columnMarkers if statement.startswith(prefix)), None)
        if marker is not None and columnMarkers[marker] in existingColumns:
            print(f"[SKIP] column already exists: bills.{columnMarkers[marker]}")
            continue
        filteredStatements.append(statement)
    return _baseMigrateUp(connection, filteredStatements)


migration.migrateUp = migrateUpDynamic


if __name__ == "__main__":
    raise SystemExit(migration.main())
