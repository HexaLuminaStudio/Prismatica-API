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
    """以 SSE 返回阶段进度、正文增量和最终结算结果。"""
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
