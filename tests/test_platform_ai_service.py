"""平台 AI 供应商用量解析测试。"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
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


class _StreamResponse:

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.encoding = ""
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        assert decode_unicode is True
        yield from self.lines

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def testIterProviderEvents_ParsesKeepAliveDeltaAndUsage() -> None:
    response = _StreamResponse(
        [
            ": keep-alive",
            'data: {"choices":[{"delta":{"content":"分析"}}],"usage":null}',
            'data: {"choices":[{"delta":{"content":"完成"}}]}',
            (
                'data: {"choices":[],"usage":{"prompt_tokens":12,'
                '"completion_tokens":8}}'
            ),
            "data: [DONE]",
        ]
    )

    assert list(service._iterProviderEvents(response)) == [
        {"event": "heartbeat", "data": {}},
        {"event": "delta", "data": {"text": "分析"}},
        {"event": "delta", "data": {"text": "完成"}},
        {
            "event": "usage",
            "data": {"inputTokens": 12, "outputTokens": 8},
        },
    ]


def testRunPlatformChatStream_StreamsThenSettlesWithProviderUsage(monkeypatch) -> None:
    response = _StreamResponse(
        [
            'data: {"choices":[{"delta":{"content":"逐段"}}]}',
            'data: {"choices":[{"delta":{"content":"返回"}}]}',
            (
                'data: {"choices":[],"usage":{"prompt_tokens":20,'
                '"completion_tokens":10}}'
            ),
            "data: [DONE]",
        ]
    )
    settings = SimpleNamespace(
        aiApiKey=SecretStr("test-key"),
        aiBaseUrl="https://example.invalid",
        aiModelChat="test-model",
        aiMaxOutputTokens=256,
        aiConnectTimeoutSec=3,
        aiReadTimeoutSec=30,
    )
    reserved = SimpleNamespace(
        billId="bill-1",
        pricingVersion="price-v1",
        estimatedCost=9,
    )
    settled = SimpleNamespace(
        realCost=3,
        refunded=6,
        balanceAfter=97,
    )
    requestCall = {}
    completed = []

    @contextmanager
    def fakeDb():
        yield object()

    def fakePost(endpoint, **kwargs):
        requestCall["endpoint"] = endpoint
        requestCall.update(kwargs)
        return response

    monkeypatch.setattr(service, "getSettings", lambda: settings)
    monkeypatch.setattr(service, "getDb", fakeDb)
    monkeypatch.setattr(service, "_claimRequest", lambda *_args: None)
    monkeypatch.setattr(service, "preauth", lambda *_args, **_kwargs: reserved)
    monkeypatch.setattr(service.requests, "post", fakePost)
    monkeypatch.setattr(service, "settleTokens", lambda *_args: settled)
    monkeypatch.setattr(
        service,
        "_completeRequest",
        lambda userId, key, result: completed.append((userId, key, result)),
    )

    requestModel = AiChatRequest(
        featureCode="ai_insight",
        messages=[{"role": "user", "content": "测试"}],
        maxOutputTokens=128,
    )
    events = list(service.runPlatformChatStream(7, requestModel, "stream-key"))

    assert [item["data"]["text"] for item in events if item["event"] == "delta"] == [
        "逐段",
        "返回",
    ]
    assert [item["data"]["stage"] for item in events if item["event"] == "progress"] == [
        "preparing",
        "preauthorizing",
        "generating",
        "settling",
        "completed",
    ]
    result = next(item["data"] for item in events if item["event"] == "completed")
    assert result["message"] == "逐段返回"
    assert result["usage"] == {
        "inputTokens": 20,
        "outputTokens": 10,
        "totalTokens": 30,
    }
    assert result["billing"]["actualCost"] == 3
    assert requestCall["stream"] is True
    assert requestCall["json"]["stream_options"] == {"include_usage": True}
    assert completed == [(7, "stream-key", result)]
    assert response.closed is True


def testRunPlatformChatStream_ClientDisconnectRefundsPendingBill(monkeypatch) -> None:
    response = _StreamResponse(
        [
            'data: {"choices":[{"delta":{"content":"部分正文"}}]}',
            'data: {"choices":[{"delta":{"content":"不应继续"}}]}',
        ]
    )
    settings = SimpleNamespace(
        aiApiKey=SecretStr("test-key"),
        aiBaseUrl="https://example.invalid",
        aiModelChat="test-model",
        aiMaxOutputTokens=256,
        aiConnectTimeoutSec=3,
        aiReadTimeoutSec=30,
    )
    reserved = SimpleNamespace(
        billId="bill-cancel",
        pricingVersion="price-v1",
        estimatedCost=9,
    )
    refunded = []
    released = []

    @contextmanager
    def fakeDb():
        yield object()

    monkeypatch.setattr(service, "getSettings", lambda: settings)
    monkeypatch.setattr(service, "getDb", fakeDb)
    monkeypatch.setattr(service, "_claimRequest", lambda *_args: None)
    monkeypatch.setattr(service, "preauth", lambda *_args, **_kwargs: reserved)
    monkeypatch.setattr(service.requests, "post", lambda *_args, **_kwargs: response)
    monkeypatch.setattr(
        service,
        "refund",
        lambda _db, billId, operation: refunded.append((billId, operation)),
    )
    monkeypatch.setattr(
        service,
        "_releaseRequest",
        lambda userId, key: released.append((userId, key)),
    )
    requestModel = AiChatRequest(
        featureCode="ai_insight",
        messages=[{"role": "user", "content": "测试取消"}],
    )
    stream = service.runPlatformChatStream(8, requestModel, "cancel-key")

    assert next(stream)["data"]["stage"] == "preparing"
    assert next(stream)["data"]["stage"] == "preauthorizing"
    assert next(stream)["data"]["stage"] == "generating"
    assert next(stream) == {"event": "delta", "data": {"text": "部分正文"}}
    stream.close()

    assert refunded == [("bill-cancel", "platform_ai.stream_refund")]
    assert released == [(8, "cancel-key")]
    assert response.closed is True
