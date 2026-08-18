from pathlib import Path

import pytest

from scripts.migrate_account_billing import loadSections, splitStatements


def testSplitStatementsIgnoresCommentsAndBlankLines() -> None:
    sql = """
    -- comment
    CREATE TABLE alpha (id BIGINT);

    CREATE TABLE beta (
        id BIGINT
    );
    """

    assert splitStatements(sql) == [
        "CREATE TABLE alpha (id BIGINT)",
        "CREATE TABLE beta (\n        id BIGINT\n    )",
    ]


def testLoadSectionsReturnsCompleteUpAndDownMigration() -> None:
    up, down = loadSections()

    assert len(up) == 14
    assert len(down) == 14
    assert up[0].startswith("CREATE TABLE IF NOT EXISTS users")
    assert down[-1] == "DROP TABLE IF EXISTS users"


def testLoadSectionsRejectsMissingMarkers(tmp_path: Path) -> None:
    migration = tmp_path / "broken.sql"
    migration.write_text("CREATE TABLE broken (id BIGINT);", encoding="utf-8")

    with pytest.raises(ValueError, match="up/down marker"):
        loadSections(migration)


def testAuthVersionMigrationHasReversibleColumnChange() -> None:
    migration = Path(__file__).resolve().parents[1] / "scripts" / "migrations" / "2026_08_17_auth_version.sql"
    up, down = loadSections(migration)

    assert up == ["ALTER TABLE users ADD COLUMN auth_version BIGINT NOT NULL DEFAULT 0 AFTER status"]
    assert down == ["ALTER TABLE users DROP COLUMN auth_version"]
