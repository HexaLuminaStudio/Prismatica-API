"""P0-A 账号计费 schema 迁移工具。

MySQL DDL 会隐式提交，无法获得真正的跨语句事务。该工具使用数据库 advisory
lock、防重复的 schema_migrations 记录，以及全部 IF NOT EXISTS DDL，使中断后可
安全重跑。down 属于破坏性操作，必须同时传入两个显式参数。
"""

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

MIGRATION_ID = "2026_08_06_account_billing_overhaul"
MIGRATION_PATH = ROOT / "scripts" / "migrations" / f"{MIGRATION_ID}.sql"
LOCK_NAME = "prismatica_schema_migration"


def splitStatements(sql: str) -> list[str]:
    """拆分当前迁移文件的简单 DDL；不支持存储过程内部分号。"""
    statements: list[str] = []
    buffer: list[str] = []
    for rawLine in sql.splitlines():
        stripped = rawLine.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(rawLine)
        if stripped.endswith(";"):
            statement = "\n".join(buffer).strip()
            statements.append(statement[:-1].rstrip())
            buffer = []
    if buffer:
        statements.append("\n".join(buffer).strip())
    return statements


def loadSections(path: Path = MIGRATION_PATH) -> tuple[list[str], list[str]]:
    sql = path.read_text(encoding="utf-8")
    upMarker = "-- migrate:up"
    downMarker = "-- migrate:down"
    if upMarker not in sql or downMarker not in sql:
        raise ValueError("迁移文件缺少 up/down marker")
    upSql, downSql = sql.split(downMarker, maxsplit=1)
    upSql = upSql.split(upMarker, maxsplit=1)[1]
    return splitStatements(upSql), splitStatements(downSql)


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
    return (
        connection.execute(
            text("SELECT 1 FROM schema_migrations WHERE migration_id = :migrationId LIMIT 1"),
            {"migrationId": MIGRATION_ID},
        ).scalar_one_or_none()
        is not None
    )


def migrateUp(connection: Connection, statements: list[str]) -> bool:
    ensureMigrationTable(connection)
    if isApplied(connection):
        print(f"[SKIP] migration already applied: {MIGRATION_ID}")
        return False
    for index, statement in enumerate(statements, start=1):
        connection.execute(text(statement))
        print(f"[UP] {index}/{len(statements)}")
    connection.execute(
        text("INSERT INTO schema_migrations (migration_id) VALUES (:migrationId)"),
        {"migrationId": MIGRATION_ID},
    )
    connection.commit()
    print(f"[OK] migration applied: {MIGRATION_ID}")
    return True


def migrateDown(connection: Connection, statements: list[str]) -> bool:
    ensureMigrationTable(connection)
    if not isApplied(connection):
        print(f"[SKIP] migration not applied: {MIGRATION_ID}")
        return False
    for index, statement in enumerate(statements, start=1):
        connection.execute(text(statement))
        print(f"[DOWN] {index}/{len(statements)}")
    connection.execute(
        text("DELETE FROM schema_migrations WHERE migration_id = :migrationId"),
        {"migrationId": MIGRATION_ID},
    )
    connection.commit()
    print(f"[OK] migration reverted: {MIGRATION_ID}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Prismatica P0-A schema migration")
    parser.add_argument("direction", nargs="?", choices=("up", "down"), default="up")
    parser.add_argument("--allow-destructive", action="store_true")
    args = parser.parse_args()
    if args.direction == "down" and not args.allow_destructive:
        parser.error("down 会删除全部 P0-A 表，必须显式传入 --allow-destructive")

    settings = Settings(_env_file=ROOT / ".env")
    engine = create_engine(settings.dbUrl, pool_pre_ping=True, future=True)
    upStatements, downStatements = loadSections()

    with engine.connect() as connection:
        acquired = connection.execute(text("SELECT GET_LOCK(:lockName, 10)"), {"lockName": LOCK_NAME}).scalar_one()
        if acquired != 1:
            print("[FAIL] 无法获取数据库迁移锁")
            return 1
        try:
            if args.direction == "up":
                migrateUp(connection, upStatements)
            else:
                migrateDown(connection, downStatements)
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:lockName)"), {"lockName": LOCK_NAME})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
