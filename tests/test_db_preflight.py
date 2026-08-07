from scripts.db_preflight import findSchemaGaps


def testFindSchemaGapsReportsMissingTablesAndColumns() -> None:
    actual = {"users": {"id"}}
    required = {
        "users": {"id", "email"},
        "subscriptions": {"id"},
    }

    assert findSchemaGaps(actual, required) == {
        "users": ["email"],
        "subscriptions": ["<table missing>"],
    }


def testFindSchemaGapsReturnsEmptyForReadySchema() -> None:
    required = {"users": {"id", "email"}}
    actual = {"users": {"id", "email", "created_at"}}

    assert findSchemaGaps(actual, required) == {}
