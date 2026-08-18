"""平台 AI 代理与服务端 Token 计费闭环。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
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


def _progressEvent(stage: str, percent: int, message: str) -> dict[str, Any]:
    """构造桌面端可直接消费的阶段进度事件。"""
    return {
        "event": "progress",
        "data": {
            "stage": stage,
            "percent": percent,
            "message": message,
        },
    }


def _errorEvent(error: ApiError) -> dict[str, Any]:
    data: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
    }
    if error.details:
        data["details"] = error.details
    return {"event": "error", "data": data}


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


def _iterProviderEvents(response) -> Iterator[dict[str, Any]]:
    """解析 OpenAI 兼容供应商的 SSE 增量与最终 usage。"""
    for rawLine in response.iter_lines(decode_unicode=True):
        if not rawLine:
            continue
        line = str(rawLine).strip()
        if not line:
            continue
        if line.startswith(":"):
            yield {"event": "heartbeat", "data": {}}
            continue
        if not line.startswith("data:"):
            continue
        rawData = line[5:].strip()
        if rawData == "[DONE]":
            break
        payload = json.loads(rawData)
        if not isinstance(payload, dict):
            raise ValueError("流式响应不是 JSON 对象")
        providerError = payload.get("error")
        if providerError:
            raise ApiError(
                "AI_UPSTREAM_UNAVAILABLE",
                "AI 服务返回流式错误",
                httpStatus=502,
            )
        usage = payload.get("usage")
        if isinstance(usage, dict):
            inputTokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
            outputTokens = int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
            if inputTokens > 0 and outputTokens > 0:
                yield {
                    "event": "usage",
                    "data": {
                        "inputTokens": inputTokens,
                        "outputTokens": outputTokens,
                    },
                }
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice, dict) else {}
        content = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(content, str) and content:
            yield {"event": "delta", "data": {"text": content}}


def _refundStreamRequest(reserved, userId: int, idempotencyKey: str) -> None:
    """释放尚未结算的流式 AI 预授权和幂等占用。"""
    try:
        if reserved is not None:
            with getDb() as db:
                refund(db, reserved.billId, operation="platform_ai.stream_refund")
    except Exception as error:
        logger.exception(f"[PlatformAI] 流式退款失败: {type(error).__name__}")
    finally:
        try:
            _releaseRequest(userId, idempotencyKey)
        except Exception as error:
            logger.exception(f"[PlatformAI] 释放流式幂等状态失败: {type(error).__name__}")


def runPlatformChatStream(
    userId: int,
    requestModel: AiChatRequest,
    idempotencyKey: str,
) -> Iterator[dict[str, Any]]:
    """流式代理平台 AI，同时维持预授权、真实 Token 结算与失败退款。"""
    settings = getSettings()
    apiKey = settings.aiApiKey.get_secret_value().strip()
    reserved = None
    isSettled = False
    providerResponse = None
    try:
        if not apiKey:
            raise ApiError("AI_SERVICE_NOT_CONFIGURED")
        yield _progressEvent("preparing", 5, "正在准备解读材料")
        messages = [item.model_dump() for item in requestModel.messages]
        requestHash = _requestHash(requestModel)
        cachedResponse = _claimRequest(userId, idempotencyKey, requestHash)
        if cachedResponse is not None:
            cachedText = str(cachedResponse.get("message", ""))
            if cachedText:
                yield {"event": "delta", "data": {"text": cachedText}}
            yield _progressEvent("completed", 100, "解读已完成")
            yield {"event": "completed", "data": cachedResponse}
            return

        inputTokenUpper = _estimateInputTokenUpperBound(messages)
        maxOutputTokens = min(
            requestModel.maxOutputTokens or settings.aiMaxOutputTokens,
            settings.aiMaxOutputTokens,
        )
        yield _progressEvent("preauthorizing", 12, "正在确认余额并预留本次费用")
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
        providerResponse = requests.post(
            endpoint,
            headers={
                "Authorization": f"Bearer {apiKey}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.aiModelChat,
                "messages": messages,
                "temperature": requestModel.temperature,
                "max_tokens": maxOutputTokens,
                "stream": True,
                "stream_options": {"include_usage": True},
            },
            timeout=(settings.aiConnectTimeoutSec, settings.aiReadTimeoutSec),
            stream=True,
        )
        providerResponse.raise_for_status()
        providerResponse.encoding = "utf-8"
        yield _progressEvent("generating", 20, "AI 正在生成解读")

        contentParts: list[str] = []
        inputTokens = 0
        outputTokens = 0
        for providerEvent in _iterProviderEvents(providerResponse):
            if providerEvent["event"] == "delta":
                text = str(providerEvent["data"].get("text", ""))
                if text:
                    contentParts.append(text)
                    yield providerEvent
            elif providerEvent["event"] == "usage":
                inputTokens = int(providerEvent["data"].get("inputTokens", 0) or 0)
                outputTokens = int(providerEvent["data"].get("outputTokens", 0) or 0)
            elif providerEvent["event"] == "heartbeat":
                yield providerEvent

        content = "".join(contentParts).strip()
        if not content:
            raise ApiError("AI_UPSTREAM_UNAVAILABLE", "AI 服务未返回有效文本", httpStatus=502)
        if inputTokens <= 0 or outputTokens <= 0:
            raise ApiError(
                "AI_UPSTREAM_UNAVAILABLE",
                "AI 服务未返回可核验的 Token 用量，本次请求不计费",
                httpStatus=502,
            )

        yield _progressEvent("settling", 92, "正文已生成，正在按实际用量结算")
        with getDb() as db:
            settled = settleTokens(db, reserved.billId, inputTokens, outputTokens)
        isSettled = True
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
        try:
            _completeRequest(userId, idempotencyKey, result)
        except Exception as error:
            logger.exception(f"[PlatformAI] 写入流式幂等结果失败: {type(error).__name__}")
        yield _progressEvent("completed", 100, "解读已完成并结算")
        yield {"event": "completed", "data": result}
    except GeneratorExit:
        if not isSettled:
            _refundStreamRequest(reserved, userId, idempotencyKey)
        raise
    except ApiError as error:
        if not isSettled:
            _refundStreamRequest(reserved, userId, idempotencyKey)
        yield _errorEvent(error)
    except (requests.RequestException, ValueError, TypeError) as error:
        logger.warning(f"[PlatformAI] 流式上游调用失败: {type(error).__name__}")
        if not isSettled:
            _refundStreamRequest(reserved, userId, idempotencyKey)
        yield _errorEvent(ApiError("AI_UPSTREAM_UNAVAILABLE"))
    except Exception as error:
        logger.exception(f"[PlatformAI] 流式处理异常: {type(error).__name__}")
        if not isSettled:
            _refundStreamRequest(reserved, userId, idempotencyKey)
        yield _errorEvent(ApiError("INTERNAL_ERROR"))
    finally:
        if providerResponse is not None:
            providerResponse.close()


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


__all__ = [
    "AI_REQUEST_OPERATION",
    "runPlatformChat",
    "runPlatformChatStream",
]
