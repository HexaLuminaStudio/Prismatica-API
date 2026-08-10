"""统一 envelope + 业务错误码(2026-08-06 重构)。

成功响应:
    {
        "code": "OK",
        "data": { ... },   # 204 场景为 null
        "requestId": "..."
    }

失败响应:
    {
        "code": "BAD_REQUEST",
        "message": "请求参数错误",
        "requestId": "...",
        "details": { ... }   # 可选
    }

错误码 HTTP 映射:
    INVALID_CODE=400 / EXPIRED=401 / ALREADY_USED=409 / ALREADY_AUTHENTICATED=409 /
    NEED_ACTIVATION=400 / INSUFFICIENT_BALANCE=402 / BILL_NOT_FOUND=409 /
    BILL_ALREADY_SETTLED=409 / BILL_NOT_PENDING=409 / RATE_LIMITED=429 /
    UNAUTHORIZED=401 / FORBIDDEN=403 / NOT_FOUND=404 / BAD_REQUEST=400 /
    CONFLICT=409 / INTERNAL_ERROR=500
    Admin 专属:
    ADMIN_LOGIN_REQUIRED=401 / ADMIN_ACCOUNT_LOCKED=423 /
    ADMIN_INVALID_CREDENTIALS=401
"""

from __future__ import annotations

from typing import Any

from flask import jsonify
from loguru import logger

# ---------------------------------------------------------------------------
# 错误码 → HTTP / 中文文案
# ---------------------------------------------------------------------------

_ERROR_HTTP: dict[str, int] = {
    "INVALID_CODE": 400,
    "EXPIRED": 401,
    "ALREADY_USED": 409,
    "ALREADY_AUTHENTICATED": 409,
    "NEED_ACTIVATION": 400,
    "INSUFFICIENT_BALANCE": 402,
    "BILL_NOT_FOUND": 409,
    "BILL_ALREADY_SETTLED": 409,
    "BILL_NOT_PENDING": 409,
    "RATE_LIMITED": 429,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "NOT_FOUND": 404,
    "BAD_REQUEST": 400,
    "CONFLICT": 409,
    "INTERNAL_ERROR": 500,
    "ADMIN_LOGIN_REQUIRED": 401,
    "ADMIN_ACCOUNT_LOCKED": 423,
    "ADMIN_INVALID_CREDENTIALS": 401,
    "TOKEN_REVOKED": 401,
    "EMAIL_ALREADY_USED": 409,
    "INVALID_CREDENTIALS": 401,
    "ACCOUNT_LOCKED": 423,
    "MAX_DEVICES_REACHED": 403,
    "REFRESH_INVALID": 401,
    "REFRESH_EXPIRED": 401,
    "RESET_TOKEN_INVALID": 400,
    "RESET_TOKEN_USED": 410,
    "RESET_TOKEN_EXPIRED": 410,
    # 2026-08-07 M9:补的错误码
    "USER_NOT_FOUND": 404,
    "DEVICE_NOT_FOUND": 404,
    "SUBSCRIPTION_NOT_FOUND": 404,
    "PLAN_NOT_FOUND": 400,
    "IDEMPOTENCY_CONFLICT": 409,
    "WEAK_PASSWORD": 400,
    "DISPLAY_NAME_INVALID": 400,
    "ACCOUNT_DELETED": 410,
    "TOO_MANY_DEVICES": 403,
    "RESOURCE_SUBSCRIPTION_REQUIRED": 403,
    "RESOURCE_NOT_CONFIGURED": 503,
    "RESOURCE_TICKET_INVALID": 401,
    "RESOURCE_TICKET_EXPIRED": 401,
    "RESOURCE_UPSTREAM_UNAVAILABLE": 502,
    "RESOURCE_DEVICE_KEY_REQUIRED": 428,
    "RESOURCE_DEVICE_KEY_INVALID": 400,
    "RESOURCE_DEVICE_KEY_CONFLICT": 409,
    "RESOURCE_KMS_UNAVAILABLE": 503,
    "RESOURCE_SIGNING_UNAVAILABLE": 503,
}

_ERROR_MESSAGE_CN: dict[str, str] = {
    "INVALID_CODE": "码无效或已损坏",
    "EXPIRED": "该凭证已过期",
    "ALREADY_USED": "该充值码已被使用",
    "ALREADY_AUTHENTICATED": "已存在激活凭证,请先注销后再兑换",
    "NEED_ACTIVATION": "请先激活后再使用充值码",
    "INSUFFICIENT_BALANCE": "余额不足",
    "BILL_NOT_FOUND": "账单不存在",
    "BILL_ALREADY_SETTLED": "账单已结算",
    "BILL_NOT_PENDING": "账单不在待结算状态",
    "RATE_LIMITED": "请求过于频繁,请稍后再试",
    "UNAUTHORIZED": "未登录或登录已过期",
    "FORBIDDEN": "权限不足",
    "NOT_FOUND": "资源不存在",
    "BAD_REQUEST": "请求参数错误",
    "CONFLICT": "资源状态冲突",
    "INTERNAL_ERROR": "服务暂时不可用,请稍后再试",
    "ADMIN_LOGIN_REQUIRED": "请先登录管理后台",
    "ADMIN_ACCOUNT_LOCKED": "管理员账号已被锁定,请联系超级管理员",
    "ADMIN_INVALID_CREDENTIALS": "用户名或密码错误",
    "TOKEN_REVOKED": "登录凭证已被撤销,请重新登录",
    "EMAIL_ALREADY_USED": "该邮箱已被注册",
    "INVALID_CREDENTIALS": "邮箱或密码错误",
    "ACCOUNT_LOCKED": "登录失败次数过多,账号已暂时锁定",
    "MAX_DEVICES_REACHED": "已达到可登录设备数量上限",
    "REFRESH_INVALID": "刷新凭证无效",
    "REFRESH_EXPIRED": "刷新凭证已过期,请重新登录",
    "RESET_TOKEN_INVALID": "密码重置凭证无效",
    "RESET_TOKEN_USED": "密码重置凭证已被使用",
    "RESET_TOKEN_EXPIRED": "密码重置凭证已过期",
    # 2026-08-07 M9
    "USER_NOT_FOUND": "用户不存在",
    "DEVICE_NOT_FOUND": "设备不存在",
    "SUBSCRIPTION_NOT_FOUND": "订阅不存在",
    "PLAN_NOT_FOUND": "订阅计划不存在",
    "IDEMPOTENCY_CONFLICT": "幂等键冲突",
    "WEAK_PASSWORD": "密码强度不足,至少 10 位且包含字母+数字",
    "DISPLAY_NAME_INVALID": "昵称不合法,长度需在 0-64 字符",
    "ACCOUNT_DELETED": "账号已注销",
    "TOO_MANY_DEVICES": "设备数量超过上限",
    "RESOURCE_SUBSCRIPTION_REQUIRED": "需要有效的试用、Pro 或 Team 订阅才能下载该资源",
    "RESOURCE_NOT_CONFIGURED": "资源下载服务尚未配置",
    "RESOURCE_TICKET_INVALID": "资源下载凭证无效",
    "RESOURCE_TICKET_EXPIRED": "资源下载凭证已过期，请重新获取",
    "RESOURCE_UPSTREAM_UNAVAILABLE": "资源服务器暂时不可用",
    "RESOURCE_DEVICE_KEY_REQUIRED": "当前设备尚未注册资源保护密钥",
    "RESOURCE_DEVICE_KEY_INVALID": "设备资源密钥或持有证明无效",
    "RESOURCE_DEVICE_KEY_CONFLICT": "当前设备已绑定其他资源密钥",
    "RESOURCE_KMS_UNAVAILABLE": "资源密钥服务暂时不可用",
    "RESOURCE_SIGNING_UNAVAILABLE": "资源清单签名服务暂时不可用",
}


# ---------------------------------------------------------------------------
# ApiError
# ---------------------------------------------------------------------------


class ApiError(Exception):
    """业务异常(自动序列化为 envelope)。

    用法:
        raise ApiError("ALREADY_USED", "该充值码已被使用")
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": [...]})
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        httpStatus: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or _ERROR_MESSAGE_CN.get(code, code)
        self.httpStatus = httpStatus or _ERROR_HTTP.get(code, 400)
        self.details = details or {}

    def toEnvelope(self) -> dict[str, Any]:
        """序列化为统一 envelope(顶层 code/message)。"""
        from flask import g

        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        requestId = getattr(g, "requestId", None)
        if requestId:
            body["requestId"] = requestId
        if self.details:
            body["details"] = self.details
        return body


# ---------------------------------------------------------------------------
# 成功响应工具
# ---------------------------------------------------------------------------


def successEnvelope(data: Any, httpStatus: int = 200) -> tuple[Any, int]:
    """生成 2xx 响应的统一 envelope。

    Args:
        data: 业务载荷(可为 dict / list / None)
        httpStatus: HTTP 状态码(默认 200;logout 等场景可用 204)
    """
    from flask import g

    body: dict[str, Any] = {"code": "OK", "data": data}
    requestId = getattr(g, "requestId", None)
    if requestId:
        body["requestId"] = requestId
    return jsonify(body), httpStatus


# ---------------------------------------------------------------------------
# 错误处理注册
# ---------------------------------------------------------------------------


def registerErrorHandlers(app) -> None:
    """注册全局错误处理(Flask app)。"""

    from flask_limiter.errors import RateLimitExceeded

    @app.errorhandler(ApiError)
    def _handleApiError(err: ApiError):
        logger.warning(f"[ApiError] {err.code}: {err.message} details={err.details}")
        return jsonify(err.toEnvelope()), err.httpStatus

    @app.errorhandler(404)
    def _handle404(_err):
        return (
            jsonify(
                {
                    "code": "NOT_FOUND",
                    "message": "接口不存在",
                    "requestId": getattr(_err, "requestId", None),
                }
            ),
            404,
        )

    @app.errorhandler(405)
    def _handle405(_err):
        return (
            jsonify({"code": "BAD_REQUEST", "message": "方法不被允许"}),
            405,
        )

    @app.errorhandler(RateLimitExceeded)
    def _handleRateLimit(_err):
        error = ApiError("RATE_LIMITED", httpStatus=429)
        return jsonify(error.toEnvelope()), 429

    @app.errorhandler(Exception)
    def _handleException(err: Exception):
        from flask import g

        logger.exception(f"[Unhandled] {type(err).__name__}: {err}")
        requestId = getattr(g, "requestId", None)
        body: dict[str, Any] = {
            "code": "INTERNAL_ERROR",
            "message": _ERROR_MESSAGE_CN["INTERNAL_ERROR"],
        }
        if requestId:
            body["requestId"] = requestId
        return jsonify(body), 500
