"""执行低成本 AI Token 定价版本迁移。"""

from __future__ import annotations

from pathlib import Path

from scripts import migrate_account_billing as migration

MIGRATION_ID = "2026_08_17_affordable_ai_pricing"
migration.MIGRATION_ID = MIGRATION_ID
migration.MIGRATION_PATH = Path(__file__).resolve().parent / "migrations" / f"{MIGRATION_ID}.sql"


if __name__ == "__main__":
    raise SystemExit(migration.main())
