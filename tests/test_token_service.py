from app.models.stored_refresh_token import StoredRefreshToken
from app.security.jwt import decode_refresh_token
from app.services.token_service import hash_token, issue_refresh_token


class FakeDb:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, value) -> None:
        self.added.append(value)

    def flush(self) -> None:
        return None


def testIssueRefreshTokenHashesAndStagesRecord() -> None:
    db = FakeDb()

    rawToken, record = issue_refresh_token(
        db,
        userId=42,
        deviceRecordId=7,
        devicePublicId="device-public-id",
        jti="refresh-jti",
    )
    claims = decode_refresh_token(rawToken)

    assert isinstance(record, StoredRefreshToken)
    assert record.jti == claims["jti"] == "refresh-jti"
    assert record.userId == 42
    assert record.deviceId == 7
    assert record.tokenHash == hash_token(rawToken)
    assert rawToken not in record.tokenHash
    assert db.added == [record]
