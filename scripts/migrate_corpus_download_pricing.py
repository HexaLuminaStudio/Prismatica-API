"""执行语料下载与 HSK 作文导出计费迁移。"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Connection

from scripts import migrate_account_billing as migration

MIGRATION_ID = "2026_08_10_corpus_download_pricing"
migration.MIGRATION_ID = MIGRATION_ID
migration.MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / f"{MIGRATION_ID}.sql"

_baseMigrateUp = migration.migrateUp


def migrateUpCorpusPricing(connection: Connection, statements: list[str]) -> bool:
    """兼容已经提前加入 unit_size 列的全新数据库。"""
    inspector = inspect(connection)
    if not inspector.has_table("pricing_rules"):
        raise RuntimeError("缺少 pricing_rules 表，请先执行动态定价迁移")
    existingColumns = {column["name"] for column in inspector.get_columns("pricing_rules")}
    filteredStatements = [
        statement
        for statement in statements
        if not (
            statement.startswith("ALTER TABLE pricing_rules ADD COLUMN unit_size")
            and "unit_size" in existingColumns
        )
    ]
    return _baseMigrateUp(connection, filteredStatements)


migration.migrateUp = migrateUpCorpusPricing


if __name__ == "__main__":
    raise SystemExit(migration.main())
