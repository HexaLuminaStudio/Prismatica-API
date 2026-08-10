"""平台 AI 供应商用量解析测试。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.errors import ApiError
from app.models.identity import User
from app.schemas.ai import AiChatRequest
from app.services import platform_ai_service as service
from app.services.platform_ai_service import _extractProviderResult


@pytest.fixture()
def aiDb(monkeypatch) -> Iterator[int]:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        user = User(
            email="ai@example.com",
            passwordHash="x",
            displayName="AI",
            tier="free",
            status="active",
        )
        db.add(user)
        db.commit()
        userId = int(user.id)

    @contextmanager
    def getTestDb():
        with factory() as db:
            yield db

    monkeypatch.setattr(service, "getDb", getTestDb)
    yield userId
    engine.dispose()


def testExtractProviderResult_UsesProviderTokenUsage() -> None:
    content, inputTokens, outputTokens = _extractProviderResult(
        {
            "choices": [{"message": {"content": "分析完成"}}],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 567},
        }
    )
    assert content == "分析完成"
    assert inputTokens == 1234
    assert outputTokens == 567


def testExtractProviderResult_MissingUsageIsRejectedInsteadOfEstimated() -> None:
    with pytest.raises(ApiError) as exc:
        _extractProviderResult({"choices": [{"message": {"content": "分析完成"}}]})
    assert exc.value.code == "AI_UPSTREAM_UNAVAILABLE"
    assert "不计费" in exc.value.message


def testAiRequestIdempotency_CachesCompletedResponse(aiDb: int) -> None:
    requestModel = AiChatRequest(messages=[{"role": "user", "content": "测试"}])
    requestHash = service._requestHash(requestModel)
    assert service._claimRequest(aiDb, "ai-key", requestHash) is None
    with pytest.raises(ApiError) as exc:
        service._claimRequest(aiDb, "ai-key", requestHash)
    assert exc.value.code == "CONFLICT"

    expected = {"message": "完成", "usage": {"inputTokens": 1, "outputTokens": 1}}
    service._completeRequest(aiDb, "ai-key", expected)
    assert service._claimRequest(aiDb, "ai-key", requestHash) == expected


def testAiRequestIdempotency_RejectsSameKeyWithDifferentBody(aiDb: int) -> None:
    first = AiChatRequest(messages=[{"role": "user", "content": "第一次"}])
    second = AiChatRequest(messages=[{"role": "user", "content": "第二次"}])
    service._claimRequest(aiDb, "same-key", service._requestHash(first))
    with pytest.raises(ApiError) as exc:
        service._claimRequest(aiDb, "same-key", service._requestHash(second))
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"
