"""/v1/auth/* 路由：邮箱密码身份 + 兼容兑换入口。"""

from __future__ import annotations

from contextlib import contextmanager

from flask import Blueprint, g, request
from pydantic import ValidationError

from app.deps import getClientIp, requireUser
from app.errors import ApiError, successEnvelope
from app.main import limiter
from app.middleware.audit_log import auditAction
from app.schemas.auth import (
    IdentityUserOut,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    PasswordChangeRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    RedeemRequest,
    RedeemResponse,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokensOut,
)
from app.services.auth_service import redeemCode
from app.services.identity_auth_service import (
    AuthResult,
    loginUser,
    logoutUser,
    refreshUserTokens,
    registerUser,
)
from app.services.password_reset_service import (
    changePassword,
    confirmPasswordReset,
    requestPasswordReset,
)

bp = Blueprint("auth", __name__, url_prefix="/v1/auth")


def _loginEmailKey() -> str:
    body = request.get_json(silent=True) or {}
    email = str(body.get("email", "")).strip().lower()
    return f"login-email:{email or 'missing'}"


def _identityUserOut(user) -> IdentityUserOut:
    return IdentityUserOut(
        userId=user.id,
        email=user.email,
        displayName=user.displayName,
        tier=user.tier,
        status=user.status,
    )


def _loginResponse(result: AuthResult) -> LoginResponse:
    return LoginResponse(
        user=_identityUserOut(result.user),
        tokens=TokensOut(
            accessToken=result.tokens.accessToken,
            refreshToken=result.tokens.refreshToken,
            expiresIn=result.tokens.expiresIn,
        ),
    )


@bp.post("/register")
@limiter.limit("5 per hour")
@auditAction("user.register", actorFrom=None, targetUserFrom=None)
def postRegister():
    try:
        payload = RegisterRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        user = registerUser(db, payload.email, payload.password, payload.displayName)
        response = RegisterResponse(user=_identityUserOut(user))
        return successEnvelope(response.model_dump(mode="json"), httpStatus=201)


@bp.post("/login")
@limiter.limit("60 per minute")
@limiter.limit("5 per 15 minutes", key_func=_loginEmailKey)
@auditAction("user.login", actorFrom=None, targetUserFrom=None)
def postLogin():
    try:
        payload = LoginRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        result = loginUser(
            db,
            payload.email,
            payload.password,
            payload.deviceId,
            payload.deviceName,
            payload.platform or request.headers.get("X-Client-Platform", ""),
            getClientIp(),
        )
        return successEnvelope(_loginResponse(result).model_dump(mode="json"))


@bp.post("/password/reset-request")
@limiter.limit("5 per hour")
def postPasswordResetRequest():
    try:
        payload = PasswordResetRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        requestPasswordReset(db, payload.email)
    return successEnvelope({"accepted": True, "message": "如果该邮箱已注册,重置邮件将很快发送"})


@bp.post("/password/reset-confirm")
def postPasswordResetConfirm():
    try:
        payload = PasswordResetConfirmRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        revokedCount = confirmPasswordReset(db, payload.token, payload.newPassword)
    return successEnvelope({"passwordReset": True, "revokedRefreshTokens": revokedCount})


@bp.post("/password/change")
@requireUser
@auditAction("user.password_change")
def postPasswordChange():
    try:
        payload = PasswordChangeRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        revokedCount = changePassword(
            db,
            g.userId,
            payload.oldPassword,
            payload.newPassword,
        )
    return successEnvelope({"passwordChanged": True, "revokedRefreshTokens": revokedCount})


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
        result = refreshUserTokens(db, payload.refreshToken, deviceId)
        return successEnvelope(_loginResponse(result).model_dump(mode="json"))


@bp.post("/logout")
def postLogout():
    try:
        body = request.get_json(force=True, silent=True) or {}
        payload = LogoutRequest.model_validate(body)
    except ValidationError:
        payload = LogoutRequest()

    with _sessionCtx() as db:
        logoutUser(db, payload.refreshToken)
    return successEnvelope(None, httpStatus=204)


# 局部工具,避免循环 import


@contextmanager
def _sessionCtx():
    from app.db import getDb

    with getDb() as db:
        yield db
