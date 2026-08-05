# coding: utf-8
"""ApiError envelope + 业务错误码。

PRD §6 错误码规范(前端 InfoBar 文案必须对齐):
    INVALID_CODE / EXPIRED / ALREADY_USED / ALREADY_AUTHENTICATED /
    NEED_ACTIVATION / INSUFFICIENT_BALANCE / BILL_NOT_FOUND /
    BILL_ALREADY_SETTLED / BILL_NOT_PENDING / RATE_LIMITED /
    INTERNAL_ERROR
"""
from __future__ import annotations

from typing import Any, Optional

from flask import jsonify, request
from loguru import logger


# 错误码 - HTTP 默认映射
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
    "INTERNAL_ERROR": 500,
}


# 错误码 - 中文文案(前端可直接显示)
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
    "INTERNAL_ERROR": "服务暂时不可用,请稍后再试",
}


class ApiError(Exception):
    """业务异常(自动序列化为 envelope)。

    使用:
        raise ApiError("ALREADY_USED", "该充值码已被使用")
    """

    def __init__(
        self,
        code: str,
        message: Optional[str] = None,
        httpStatus: Optional[int] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or _ERROR_MESSAGE_CN.get(code, code)
        self.httpStatus = httpStatus or _ERROR_HTTP.get(code, 400)
        self.details = details or {}

    def toDict(self) -> dict[str, Any]:
        """序列化为 PRD §5.1 定义的 envelope。"""
        from flask import g

        requestId = getattr(g, "requestId", None)
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if requestId:
            body["requestId"] = requestId
        if self.details:
            body["details"] = self.details
        return {"error": body}


def registerErrorHandlers(app) -> None:
    """注册全局错误处理(Flask app)。"""

    @app.errorhandler(ApiError)
    def _handleApiError(err: ApiError):
        logger.warning(f"[ApiError] {err.code}: {err.message} details={err.details}")
        resp = jsonify(err.toDict())
        resp.status_code = err.httpStatus
        return resp

    @app.errorhandler(404)
    def _handle404(_err):
        return jsonify({"error": {"code": "NOT_FOUND", "message": "接口不存在"}}), 404

    @app.errorhandler(405)
    def _handle405(_err):
        return jsonify({"error": {"code": "BAD_REQUEST", "message": "方法不被允许"}}), 405

    @app.errorhandler(Exception)
    def _handleException(err: Exception):
        logger.exception(f"[Unhandled] {type(err).__name__}: {err}")
        body = {
            "error": {
                "code": "INTERNAL_ERROR",
                "message": _ERROR_MESSAGE_CN["INTERNAL_ERROR"],
            }
        }
        from flask import g

        if getattr(g, "requestId", None):
            body["error"]["requestId"] = g.requestId
        return jsonify(body), 500