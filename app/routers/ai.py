"""平台 AI 路由。"""
from __future__ import annotations

from flask import Blueprint, g, request
from pydantic import ValidationError

from app.deps import requireUser
from app.errors import ApiError, successEnvelope
from app.schemas.ai import AiChatRequest
from app.services.platform_ai_service import runPlatformChat

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


__all__ = ["bp"]
