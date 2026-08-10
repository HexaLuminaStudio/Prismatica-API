"""平台 AI 代理与服务端 Token 计费闭环。"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import getSettings
from app.db import getDb
from app.errors import ApiError
from app.models.idempotency_key import IdempotencyKey
from app.schemas.ai import AiChatRequest
from app.services.billing_service import preauth, refund, settleTokens

AI_REQUEST_OPERATION = "platform_ai.request"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _requestHash(requestModel: AiChatRequest) -> str:
    payload = requestModel.model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _claimRequest(userId: int, idempotencyKey: str, requestHash: str) -> dict[str, Any] | None:
    """占用 AI 请求幂等键；已完成时直接返回缓存响应。"""
    with getDb() as db:
        row = db.execute(
            select(IdempotencyKey)
            .where(
                IdempotencyKey.userId == userId,
                IdempotencyKey.operation == AI_REQUEST_OPERATION,
                IdempotencyKey.idempotencyKey == idempotencyKey,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is not None and row.expiresAt <= _now():
            db.delete(row)
            db.flush()
            row = None
        if row is not None:
            if row.requestHash != requestHash:
                raise ApiError("IDEMPOTENCY_CONFLICT", "幂等键与历史 AI 请求不匹配", httpStatus=409)
            if row.responseStatus == 200 and row.responseBody is not None:
                return dict(row.responseBody)
            raise ApiError("CONFLICT", "相同 AI 请求正在处理中，请稍后重试", httpStatus=409)
        db.add(
            IdempotencyKey(
                userId=userId,
                operation=AI_REQUEST_OPERATION,
                idempotencyKey=idempotencyKey,
                requestHash=requestHash,
                responseStatus=None,
                responseBody=None,
                resourceType="ai_request",
                resourceId=None,
                expiresAt=_now() + timedelta(minutes=15),
            )
        )
        try:
            db.commit()
        except IntegrityError as error:
            db.rollback()
            raise ApiError("CONFLICT", "相同 AI 请求正在处理中，请稍后重试", httpStatus=409) from error
        return None


def _releaseRequest(userId: int, idempotencyKey: str) -> None:
    with getDb() as db:
        row = db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.userId == userId,
                IdempotencyKey.operation == AI_REQUEST_OPERATION,
                IdempotencyKey.idempotencyKey == idempotencyKey,
            )
        ).scalar_one_or_none()
        if row is not None and row.responseStatus is None:
            db.delete(row)
            db.commit()


def _completeRequest(userId: int, idempotencyKey: str, responseBody: dict[str, Any]) -> None:
    with getDb() as db:
        row = db.execute(
            select(IdempotencyKey)
            .where(
                IdempotencyKey.userId == userId,
                IdempotencyKey.operation == AI_REQUEST_OPERATION,
                IdempotencyKey.idempotencyKey == idempotencyKey,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise ApiError("CONFLICT", "AI 请求幂等状态已失效", httpStatus=409)
        row.responseStatus = 200
        row.responseBody = responseBody
        row.expiresAt = _now() + timedelta(hours=24)
        db.commit()


def _estimateInputTokenUpperBound(messages: list[dict[str, str]]) -> int:
    """生成偏保守的预占上界；实际费用只看供应商 usage。"""
    return max(1, sum(len(item["content"].encode("utf-8")) + 64 for item in messages))


def _extractProviderResult(payload: dict[str, Any]) -> tuple[str, int, int]:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ApiError("AI_UPSTREAM_UNAVAILABLE", "AI 服务返回内容不完整", httpStatus=502)
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ApiError("AI_UPSTREAM_UNAVAILABLE", "AI 服务未返回有效文本", httpStatus=502)
    usage = payload.get("usage") or {}
    inputTokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
    outputTokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
    if inputTokens <= 0 or outputTokens <= 0:
        raise ApiError(
            "AI_UPSTREAM_UNAVAILABLE",
            "AI 服务未返回可核验的 Token 用量，本次请求不计费",
            httpStatus=502,
        )
    return content, inputTokens, outputTokens


def runPlatformChat(userId: int, requestModel: AiChatRequest, idempotencyKey: str) -> dict[str, Any]:
    settings = getSettings()
    apiKey = settings.aiApiKey.get_secret_value().strip()
    if not apiKey:
        raise ApiError("AI_SERVICE_NOT_CONFIGURED")
    messages = [item.model_dump() for item in requestModel.messages]
    requestHash = _requestHash(requestModel)
    cachedResponse = _claimRequest(userId, idempotencyKey, requestHash)
    if cachedResponse is not None:
        return cachedResponse
    inputTokenUpper = _estimateInputTokenUpperBound(messages)
    maxOutputTokens = min(requestModel.maxOutputTokens or settings.aiMaxOutputTokens, settings.aiMaxOutputTokens)

    try:
        with getDb() as db:
            reserved = preauth(
                db,
                userId,
                requestModel.featureCode,
                resourceUsed=0,
                taskId=f"platform-ai:{idempotencyKey[:16]}",
                description=f"平台 AI · {requestModel.featureCode}",
                idempotencyKey=idempotencyKey,
                operation="platform_ai.preauth",
                estimatedInputTokens=inputTokenUpper,
                estimatedOutputTokens=maxOutputTokens,
            )
    except Exception:
        _releaseRequest(userId, idempotencyKey)
        raise

    endpoint = settings.aiBaseUrl.rstrip("/") + "/chat/completions"
    try:
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {apiKey}", "Content-Type": "application/json"},
            json={
                "model": settings.aiModelChat,
                "messages": messages,
                "temperature": requestModel.temperature,
                "max_tokens": maxOutputTokens,
                "stream": False,
            },
            timeout=(settings.aiConnectTimeoutSec, settings.aiReadTimeoutSec),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("响应不是 JSON 对象")
        content, inputTokens, outputTokens = _extractProviderResult(payload)
    except ApiError:
        try:
            with getDb() as db:
                refund(db, reserved.billId, operation="platform_ai.refund")
        finally:
            _releaseRequest(userId, idempotencyKey)
        raise
    except (requests.RequestException, ValueError, TypeError) as error:
        logger.warning(f"[PlatformAI] 上游调用失败: {type(error).__name__}")
        try:
            with getDb() as db:
                refund(db, reserved.billId, operation="platform_ai.refund")
        finally:
            _releaseRequest(userId, idempotencyKey)
        raise ApiError("AI_UPSTREAM_UNAVAILABLE") from error

    with getDb() as db:
        settled = settleTokens(db, reserved.billId, inputTokens, outputTokens)
    result = {
        "message": content,
        "model": settings.aiModelChat,
        "usage": {
            "inputTokens": inputTokens,
            "outputTokens": outputTokens,
            "totalTokens": inputTokens + outputTokens,
        },
        "billing": {
            "billId": reserved.billId,
            "pricingVersion": reserved.pricingVersion,
            "estimatedCost": reserved.estimatedCost,
            "actualCost": settled.realCost,
            "refunded": settled.refunded,
            "balanceAfter": settled.balanceAfter,
        },
    }
    _completeRequest(userId, idempotencyKey, result)
    return result


__all__ = ["AI_REQUEST_OPERATION", "runPlatformChat"]
