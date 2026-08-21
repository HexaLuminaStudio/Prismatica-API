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
    """用户邮箱注册。

    注册成功后返回用户信息,不直接返回 token,请调用 /login 登录。

    ---
    tags: [auth]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [email, password]
            properties:
              email:
                type: string
                format: email
                example: user@example.com
              password:
                type: string
                minLength: 10
                description: 至少 10 位且包含字母+数字
              displayName:
                type: string
                maxLength: 64
                description: 昵称(可选)
    responses:
      201:
        description: 注册成功(返回用户信息,不含 token)
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                user:
                  type: object
                  properties:
                    userId: {type: string}
                    email: {type: string}
                    displayName: {type: string}
                    tier: {type: string}
                    status: {type: string}
            requestId: {type: string}
      400:
        description: 请求参数错误(BAD_REQUEST)
      409:
        description: 邮箱已被注册(EMAIL_ALREADY_USED)
    """
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
    """邮箱密码登录。

    成功返回用户信息 + 一对 token(access / refresh)。后续请求携带
    Authorization: Bearer <accessToken>。

    ---
    tags: [auth]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [email, password, deviceId]
            properties:
              email:
                type: string
                format: email
                example: user@example.com
              password:
                type: string
                description: 登录密码
              deviceId:
                type: string
                maxLength: 64
                description: 客户端设备 UUID(用于设备绑定与刷新令牌校验)
              deviceName:
                type: string
                maxLength: 128
                description: 设备名(可选,脱敏存储)
              platform:
                type: string
                maxLength: 32
                description: 平台标识(可选,如 windows / macos / linux)
    responses:
      200:
        description: 登录成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                user:
                  type: object
                  properties:
                    userId: {type: integer}
                    email: {type: string}
                    displayName: {type: string}
                    tier: {type: string}
                    status: {type: string}
                tokens:
                  type: object
                  properties:
                    accessToken: {type: string}
                    refreshToken: {type: string}
                    expiresIn: {type: integer}
            requestId: {type: string}
      400:
        description: 请求参数错误(BAD_REQUEST)
      401:
        description: 邮箱或密码错误(INVALID_CREDENTIALS)
      423:
        description: 账号被锁定或禁用(ACCOUNT_LOCKED / ACCOUNT_DISABLED)
      429:
        description: 触发限流(TOO_MANY_REQUESTS)
    """
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
    """发起密码重置(发送重置邮件)。

    无论邮箱是否注册都返回成功,避免枚举用户。重置邮件中的 token 用于
    /password/reset-confirm。

    ---
    tags: [auth]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [email]
            properties:
              email:
                type: string
                format: email
                example: user@example.com
    responses:
      200:
        description: 已受理(不暴露邮箱是否注册)
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                accepted: {type: boolean, example: true}
                message: {type: string}
            requestId: {type: string}
      400:
        description: 请求参数错误(BAD_REQUEST)
      429:
        description: 触发限流(TOO_MANY_REQUESTS)
    """
    try:
        payload = PasswordResetRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        requestPasswordReset(db, payload.email)
    return successEnvelope({"accepted": True, "message": "如果该邮箱已注册,重置邮件将很快发送"})


@bp.post("/password/reset-confirm")
def postPasswordResetConfirm():
    """用重置邮件中的 token 设置新密码。

    成功后该用户的所有 refresh token 将被吊销。

    ---
    tags: [auth]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [token, newPassword]
            properties:
              token:
                type: string
                minLength: 20
                description: 重置邮件中的一次性 token
              newPassword:
                type: string
                minLength: 10
                description: 新密码(至少 10 位且包含字母+数字)
    responses:
      200:
        description: 重置成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                passwordReset: {type: boolean, example: true}
                revokedRefreshTokens: {type: integer, example: 1}
            requestId: {type: string}
      400:
        description: token 无效/过期或参数错误(BAD_REQUEST / INVALID_TOKEN)
    """
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
    """已登录用户修改密码。

    需要 Authorization: Bearer <accessToken>。成功后该用户的所有
    refresh token 将被吊销,需重新登录。

    ---
    tags: [auth]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [oldPassword, newPassword]
            properties:
              oldPassword:
                type: string
                description: 当前密码
              newPassword:
                type: string
                minLength: 10
                description: 新密码(至少 10 位且包含字母+数字)
    responses:
      200:
        description: 修改成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                passwordChanged: {type: boolean, example: true}
                revokedRefreshTokens: {type: integer, example: 1}
            requestId: {type: string}
      400:
        description: 参数错误(BAD_REQUEST)
      401:
        description: 未登录或旧密码错误(UNAUTHORIZED / WRONG_PASSWORD)
    """
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
    """兑换邀请码 / 试用码 / 充值码。

    兼容旧入口:一次兑换同时创建用户(如不存在)、发放权益并返回 token。
    等价能力请优先使用 /v1/auth/register + /v1/auth/login。

    ---
    tags: [auth]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [code, deviceId]
            properties:
              code:
                type: string
                description: INV/TRY/RCH 码(base64 签名载荷)
              deviceId:
                type: string
                description: 客户端设备 UUID
              deviceName:
                type: string
                description: 设备名(可选,脱敏)
              displayName:
                type: string
                default: 内测用户
                description: 用户显示名
    responses:
      200:
        description: 兑换成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                mode: {type: string, example: invite, description: invite / trial / recharge}
                user:
                  type: object
                  properties:
                    userId: {type: string}
                    displayName: {type: string}
                    tier: {type: string}
                    createdAt: {type: string, format: date-time}
                    expireAt: {type: string, format: date-time, nullable: true}
                balance:
                  type: object
                  properties:
                    balance: {type: integer}
                    frozenBalance: {type: integer}
                    totalSpent: {type: integer}
                    totalRecharged: {type: integer}
                tokens:
                  type: object
                  properties:
                    accessToken: {type: string}
                    refreshToken: {type: string}
                    expiresIn: {type: integer}
            requestId: {type: string}
      400:
        description: 参数错误(BAD_REQUEST)
      404:
        description: 兑换码不存在或已失效(CODE_INVALID / CODE_NOT_FOUND)
    """
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
    """用 refresh token 换取新的 token 对。

    请求头 X-Device-Id 必须与签发 refresh token 时的设备一致。

    ---
    tags: [auth]
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [refreshToken]
            properties:
              refreshToken:
                type: string
                description: 带 jti 的签名 Refresh JWT
    responses:
      200:
        description: 刷新成功(返回新的 token 对)
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                user:
                  type: object
                  properties:
                    userId: {type: integer}
                    email: {type: string}
                    displayName: {type: string}
                    tier: {type: string}
                    status: {type: string}
                tokens:
                  type: object
                  properties:
                    accessToken: {type: string}
                    refreshToken: {type: string}
                    expiresIn: {type: integer}
            requestId: {type: string}
      400:
        description: 参数错误(BAD_REQUEST)
      401:
        description: refresh token 无效/过期或设备不匹配(UNAUTHORIZED)
    """
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
    """退出登录(吊销 refresh token)。

    可携带待吊销的 refreshToken;不传则视为客户端仅清除本地凭据。

    ---
    tags: [auth]
    requestBody:
      required: false
      content:
        application/json:
          schema:
            type: object
            properties:
              refreshToken:
                type: string
                description: 待吊销的 refresh token(可选)
    responses:
      204:
        description: 退出成功(无响应体)
      400:
        description: 参数错误(BAD_REQUEST)
    """
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
