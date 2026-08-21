"""平台 AI 路由。"""

from __future__ import annotations

import json

from flask import Blueprint, Response, g, request, stream_with_context
from pydantic import ValidationError

from app.deps import requireUser
from app.errors import ApiError, successEnvelope
from app.schemas.ai import AiChatRequest
from app.services.platform_ai_service import runPlatformChat, runPlatformChatStream

bp = Blueprint("ai", __name__, url_prefix="/v1/ai")


@bp.post("/chat")
@requireUser
def platformChatRoute():
    """平台 AI 聊天(非流式,按 Token 计费)。

    需携带 Idempotency-Key 请求头,同一 key 重复调用返回同一结果。

    ---
    tags: [ai]
    security:
      - bearerAuth: []
    parameters:
      - name: Idempotency-Key
        in: header
        required: true
        schema:
          type: string
        description: 幂等键(≤64 字符)
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [messages]
            properties:
              featureCode:
                type: string
                enum: [ai_chat, ai_insight, ai_report]
                default: ai_chat
              messages:
                type: array
                maxItems: 80
                items:
                  type: object
                  required: [role, content]
                  properties:
                    role: {type: string, enum: [system, user, assistant]}
                    content: {type: string}
                description: 对话历史(总长度 ≤ 400000 字符)
              maxOutputTokens:
                type: integer
                minimum: 128
                maximum: 32768
                nullable: true
              temperature:
                type: number
                format: float
                default: 0.3
                minimum: 0.0
                maximum: 1.5
    responses:
      200:
        description: AI 响应(含计费结算结果)
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              description: 含 reply / usage / billing 等字段
            requestId: {type: string}
      400:
        description: 请求参数无效或缺少 Idempotency-Key(BAD_REQUEST)
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
      402:
        description: 余额不足(INSUFFICIENT_BALANCE)
    """
    idempotencyKey = request.headers.get("Idempotency-Key", "").strip()
    if not idempotencyKey or len(idempotencyKey) > 64:
        raise ApiError("BAD_REQUEST", "缺少有效的 Idempotency-Key")
    try:
        requestModel = AiChatRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "AI 请求参数无效", details={"errors": error.errors()}) from error
    return successEnvelope(runPlatformChat(int(g.userId), requestModel, idempotencyKey))


@bp.post("/chat/stream")
@requireUser
def platformChatStreamRoute():
    """以 SSE 返回阶段进度、正文增量和最终结算结果。

    事件(event)说明:
    - progress:阶段进度
    - delta:正文增量
    - done:最终结算结果

    ---
    tags: [ai]
    security:
      - bearerAuth: []
    parameters:
      - name: Idempotency-Key
        in: header
        required: true
        schema:
          type: string
        description: 幂等键(≤64 字符)
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [messages]
            properties:
              featureCode:
                type: string
                enum: [ai_chat, ai_insight, ai_report]
                default: ai_chat
              messages:
                type: array
                maxItems: 80
                items:
                  type: object
                  required: [role, content]
                  properties:
                    role: {type: string, enum: [system, user, assistant]}
                    content: {type: string}
              maxOutputTokens:
                type: integer
                minimum: 128
                maximum: 32768
                nullable: true
              temperature:
                type: number
                format: float
                default: 0.3
                minimum: 0.0
                maximum: 1.5
    responses:
      200:
        description: text/event-stream 流(progress / delta / done 事件)
      400:
        description: 请求参数无效或缺少 Idempotency-Key(BAD_REQUEST)
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
      402:
        description: 余额不足(INSUFFICIENT_BALANCE)
    """
    idempotencyKey = request.headers.get("Idempotency-Key", "").strip()
    if not idempotencyKey or len(idempotencyKey) > 64:
        raise ApiError("BAD_REQUEST", "缺少有效的 Idempotency-Key")
    try:
        requestModel = AiChatRequest.model_validate(request.get_json(silent=True) or {})
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "AI 请求参数无效", details={"errors": error.errors()}) from error
    userId = int(g.userId)

    def _stream():
        for item in runPlatformChatStream(userId, requestModel, idempotencyKey):
            eventName = str(item.get("event", "message"))
            data = json.dumps(
                item.get("data") or {},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"event: {eventName}\ndata: {data}\n\n"

    response = Response(
        stream_with_context(_stream()),
        mimetype="text/event-stream",
    )
    response.headers["Cache-Control"] = "no-cache, no-transform"
    response.headers["X-Accel-Buffering"] = "no"
    return response


__all__ = ["bp"]
