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
            jsonify(
                {"code": "BAD_REQUEST", "message": "方法不被允许"}
            ),
            405,
        )

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