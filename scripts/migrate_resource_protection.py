"""资源保护设备密钥字段的版本化 MySQL 迁移工具。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402

MIGRATION_ID = "2026_08_10_resource_protection"
MIGRATION_PATH = ROOT / "scripts" / "migrations" / f"{MIGRATION_ID}.sql"
LOCK_NAME = "prismatica_schema_migration"
COLUMN_DEFINITIONS = (
    (
        "resource_encryption_public_key",
        "VARCHAR(64) NULL AFTER revoked_at",
    ),
    (
        "resource_signing_public_key",
        "VARCHAR(64) NULL AFTER resource_encryption_public_key",
    ),
    (
        "resource_key_updated_at",
        "DATETIME(3) NULL AFTER resource_signing_public_key",
    ),
)


def splitStatements(sql: str) -> list[str]:
    """拆分不含存储过程的迁移 SQL。"""
    statements: list[str] = []
    buffer: list[str] = []
    for rawLine in sql.splitlines():
        stripped = rawLine.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(rawLine)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).strip()[:-1].rstrip())
            buffer = []
    if buffer:
        statements.append("\n".join(buffer).strip())
    return statements


def loadSections() -> tuple[list[str], list[str]]:
    """读取 up/down 两个迁移段。"""
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    upMarker = "-- migrate:up"
    downMarker = "-- migrate:down"
    if upMarker not in sql or downMarker not in sql:
        raise ValueError("迁移文件缺少 up/down marker")
    upSql, downSql = sql.split(downMarker, maxsplit=1)
    return splitStatements(upSql.split(upMarker, maxsplit=1)[1]), splitStatements(downSql)


def ensureMigrationTable(connection: Connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "migration_id VARCHAR(128) NOT NULL PRIMARY KEY, "
            "applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci"
        )
    )


def isApplied(connection: Connection) -> bool:
    return connection.execute(
        text("SELECT 1 FROM schema_migrations WHERE migration_id=:migrationId LIMIT 1"),
        {"migrationId": MIGRATION_ID},
    ).scalar_one_or_none() is not None


def existingColumns(connection: Connection) -> set[str]:
    """读取当前库 user_devices 的真实列，支持中断后安全重跑。"""
    rows = connection.execute(
        text(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='user_devices'"
        )
    ).scalars()
    return {str(columnName) for columnName in rows}


def migrateUp(connection: Connection) -> None:
    ensureMigrationTable(connection)
    if isApplied(connection):
        print(f"[SKIP] migration already applied: {MIGRATION_ID}")
        return
    columns = existingColumns(connection)
    if not columns:
        raise RuntimeError("user_devices 表不存在，请先执行账号计费基础迁移")
    for index, (columnName, definition) in enumerate(COLUMN_DEFINITIONS, start=1):
        if columnName in columns:
            print(f"[SKIP] column exists: {columnName}")
            continue
        connection.execute(
            text(f"ALTER TABLE user_devices ADD COLUMN {columnName} {definition}")
        )
        columns.add(columnName)
        print(f"[UP] {index}/{len(COLUMN_DEFINITIONS)}")
    connection.execute(
        text("INSERT INTO schema_migrations (migration_id) VALUES (:migrationId)"),
        {"migrationId": MIGRATION_ID},
    )
    connection.commit()
    print(f"[OK] migration applied: {MIGRATION_ID}")


def migrateDown(connection: Connection) -> None:
    ensureMigrationTable(connection)
    if not isApplied(connection):
        print(f"[SKIP] migration not applied: {MIGRATION_ID}")
        return
    columns = existingColumns(connection)
    reversedColumns = tuple(reversed(COLUMN_DEFINITIONS))
    for index, (columnName, _definition) in enumerate(reversedColumns, start=1):
        if columnName not in columns:
            print(f"[SKIP] column absent: {columnName}")
            continue
        connection.execute(text(f"ALTER TABLE user_devices DROP COLUMN {columnName}"))
        columns.remove(columnName)
        print(f"[DOWN] {index}/{len(reversedColumns)}")
    connection.execute(
        text("DELETE FROM schema_migrations WHERE migration_id=:migrationId"),
        {"migrationId": MIGRATION_ID},
    )
    connection.commit()
    print(f"[OK] migration reverted: {MIGRATION_ID}")


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移资源保护设备密钥字段")
    parser.add_argument("direction", choices=("up", "down"), nargs="?", default="up")
    parser.add_argument(
        "--allow-destructive",
        dest="allowDestructive",
        action="store_true",
    )
    arguments = parser.parse_args()
    if arguments.direction == "down" and not arguments.allowDestructive:
        parser.error("down 会删除设备公钥，必须显式传入 --allow-destructive")

    settings = Settings()
    loadSections()
    engine = create_engine(settings.dbUrl, future=True)
    try:
        with engine.connect() as connection:
            acquired = connection.execute(
                text("SELECT GET_LOCK(:lockName, 30)"),
                {"lockName": LOCK_NAME},
            ).scalar_one()
            if acquired != 1:
                raise RuntimeError("无法获取数据库迁移锁")
            try:
                if arguments.direction == "up":
                    migrateUp(connection)
                else:
                    migrateDown(connection)
            finally:
                connection.execute(
                    text("SELECT RELEASE_LOCK(:lockName)"),
                    {"lockName": LOCK_NAME},
                )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
