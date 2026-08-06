"""/v1/auth/* 路由:redeem / refresh / logout。"""
from __future__ import annotations

from contextlib import contextmanager

from flask import Blueprint, request
from pydantic import ValidationError

from app.deps import getClientIp
from app.errors import ApiError, successEnvelope
from app.schemas.auth import (
    LogoutRequest,
    RedeemRequest,
    RedeemResponse,
    RefreshRequest,
)
from app.services.auth_service import (
    redeemCode,
    refreshTokens,
    revokeRefreshToken,
)

bp = Blueprint("auth", __name__, url_prefix="/v1/auth")


@bp.post("/redeem")
def postRedeem():
    try:
        payload = RedeemRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    with _sessionCtx() as db:
        result: RedeemResponse = redeemCode(
            db,
            rawCode=payload.code,
            deviceId=payload.deviceId,
            deviceName=payload.deviceName,
            platform=request.headers.get("X-Client-Platform", ""),
            displayName=payload.displayName,
            clientIp=getClientIp(),
        )
        return successEnvelope(result.model_dump(mode="json"))


@bp.post("/refresh")
def postRefresh():
    try:
        payload = RefreshRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    deviceId = request.headers.get("X-Device-Id", "")
    with _sessionCtx() as db:
        result = refreshTokens(db, payload.refreshToken, deviceId)
        return successEnvelope(result.model_dump(mode="json"))


@bp.post("/logout")
def postLogout():
    try:
        body = request.get_json(force=True, silent=True) or {}
        payload = LogoutRequest.model_validate(body)
    except ValidationError:
        payload = LogoutRequest()

    with _sessionCtx() as db:
        revokeRefreshToken(db, payload.refreshToken)
    return successEnvelope(None, httpStatus=204)


# 局部工具,避免循环 import


@contextmanager
def _sessionCtx():
    from app.db import getDb

    with getDb() as db:
        yield db
