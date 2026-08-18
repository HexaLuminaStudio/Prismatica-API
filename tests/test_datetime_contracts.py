"""API 时间字段统一使用 UTC 的回归测试。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.datetime_utils import parseUtcIso, toUtcIso
from app.schemas.admin import AdminAuditItem


def testUtcResponseSerializesNaiveDatabaseTimeWithZ() -> None:
    item = AdminAuditItem(
        auditId=1,
        actor="admin",
        action="user.pause",
        createdAt=datetime(2026, 8, 17, 3, 20, 0),
    )

    payload = item.model_dump(mode="json")

    assert payload["createdAt"] == "2026-08-17T03:20:00Z"


def testUtcCursorConvertsOffsetInsteadOfDroppingIt() -> None:
    parsed = parseUtcIso("2026-08-17T11:20:00+08:00")

    assert parsed == datetime(2026, 8, 17, 3, 20, 0)
    assert toUtcIso(parsed) == "2026-08-17T03:20:00Z"
    assert parsed.tzinfo is None
    assert datetime.now(UTC).tzinfo is UTC
