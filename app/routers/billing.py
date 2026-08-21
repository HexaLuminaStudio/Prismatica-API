"""/v1/billing/* 路由:estimate / preauth / settle / refund。"""

from __future__ import annotations

from contextlib import contextmanager

from flask import Blueprint, g, request
from pydantic import ValidationError
from sqlalchemy import select

from app.db import getDb
from app.deps import requireUser
from app.errors import ApiError, successEnvelope
from app.models.bill import Bill
from app.schemas.billing import (
    EstimateRequest,
    PreauthRequest,
    RefundRequest,
    SettleRequest,
)
from app.services.billing_service import getBillingService

bp = Blueprint("billing", __name__, url_prefix="/v1/billing")


@contextmanager
def _sessionCtx():
    with getDb() as db:
        yield db


def _ownedBill(db, billId: str) -> Bill:
    bill = db.execute(select(Bill).where(Bill.billId == billId)).scalar_one_or_none()
    if bill is None:
        raise ApiError("BILL_NOT_FOUND", httpStatus=409)
    if int(bill.userId) != int(g.userId):
        raise ApiError("FORBIDDEN", "不能操作其他用户的账单", httpStatus=403)
    return bill


@bp.post("/estimate")
@requireUser
def postEstimate():
    """预估一次动作的扣费(不落账单,不冻结积分)。

    ---
    tags: [billing]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [actionType, resourceUsed]
            properties:
              actionType:
                type: string
                description: freq_analyze / kwic_search 等动作标识
              resourceUsed:
                type: integer
                minimum: 0
                description: 资源量(千字或次数)
    responses:
      200:
        description: 成本预估
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                actionType: {type: string}
                displayName: {type: string}
                resourceUsed: {type: integer}
                unitName: {type: string}
                estimatedCost: {type: integer}
                currentBalance: {type: integer}
                balanceAfter: {type: integer}
                affordable: {type: boolean}
                tierBreakdown: {type: array, items: {type: object}}
                pricingVersion: {type: string}
                billingMode: {type: string}
            requestId: {type: string}
      400:
        description: 参数错误(BAD_REQUEST)
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
    """
    try:
        payload = EstimateRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    with _sessionCtx() as db:
        preview = getBillingService().estimate(
            db,
            userId=g.userId,
            actionType=payload.actionType,
            resourceUsed=payload.resourceUsed,
        )
        return successEnvelope(preview.model_dump())


@bp.post("/preauth")
@requireUser
def postPreauth():
    """预占积分并创建账单(幂等,需 Idempotency-Key 请求头)。

    调用方在发起昂贵动作前先预占,防止并发超扣;完成后用
    /commit-fixed 或 /commit-metered 结算。

    ---
    tags: [billing]
    security:
      - bearerAuth: []
    parameters:
      - name: Idempotency-Key
        in: header
        required: true
        schema:
          type: string
        description: 幂等键(同一 key 重复调用返回同一账单)
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [actionType, resourceUsed]
            properties:
              actionType:
                type: string
                description: freq_analyze / kwic_search 等动作标识
              resourceUsed:
                type: integer
                minimum: 0
              taskId:
                type: string
                description: 关联任务 ID(可选)
              description:
                type: string
                description: 账单描述(可选)
    responses:
      200:
        description: 预占成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                billId: {type: string}
                estimatedCost: {type: integer}
                balanceAfter: {type: integer}
                pricingVersion: {type: string}
                billingMode: {type: string}
            requestId: {type: string}
      400:
        description: 参数错误(BAD_REQUEST)
      401:
        description: 未登录或 token 失效(UNAUTHORIZED)
      402:
        description: 余额不足(INSUFFICIENT_BALANCE)
      409:
        description: 幂等键冲突或动作类型无效(IDEMPOTENCY_CONFLICT / PRICING_RULE_INVALID)
    """
    try:
        payload = PreauthRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    idemKey = request.headers.get("Idempotency-Key") or None
    with _sessionCtx() as db:
        result = getBillingService().preauth(
            db,
            userId=g.userId,
            actionType=payload.actionType,
            resourceUsed=payload.resourceUsed,
            taskId=payload.taskId,
            description=payload.description,
            idempotencyKey=idemKey,
        )
        return successEnvelope(result.model_dump())


@bp.post("/settle")
@requireUser
def postSettle():
    """(已停用)直接结算账单。

    该端点仅用于校验定价规则,实际结算请使用服务端受控的
    /commit-fixed 与 /commit-metered。

    ---
    tags: [billing]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [billId, realCost]
            properties:
              billId: {type: string}
              realCost: {type: integer, minimum: 0}
              resourceUsed: {type: integer, minimum: 0, default: 0}
    responses:
      409:
        description: 必须使用服务端受控结算端点(PRICING_RULE_INVALID)
      403:
        description: 不能操作其他用户的账单(FORBIDDEN)
      404:
        description: 账单不存在(BILL_NOT_FOUND)
    """
    try:
        payload = SettleRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    with _sessionCtx() as db:
        bill = _ownedBill(db, payload.billId)
        mode = str((bill.pricingSnapshot or {}).get("billingMode", "metered"))
        raise ApiError(
            "PRICING_RULE_INVALID",
            f"{mode or '未知模式'}账单必须使用服务端受控结算端点",
            httpStatus=409,
        )


@bp.post("/refund")
@requireUser
def postRefund():
    """退还未结算账单的预占金额。

    仅在预占后未真正产生资源消耗时使用(如动作失败)。

    ---
    tags: [billing]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [billId]
            properties:
              billId: {type: string}
    responses:
      200:
        description: 退款成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                billId: {type: string}
                refundedAmount: {type: integer}
                balanceAfter: {type: integer}
            requestId: {type: string}
      403:
        description: 不能操作其他用户的账单(FORBIDDEN)
      404:
        description: 账单不存在(BILL_NOT_FOUND)
      409:
        description: 账单已结算,无法退款(BILL_ALREADY_SETTLED)
    """
    try:
        payload = RefundRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()}) from e

    with _sessionCtx() as db:
        _ownedBill(db, payload.billId)
        result = getBillingService().refund(db, billId=payload.billId)
        return successEnvelope(result.model_dump())


@bp.post("/commit-fixed")
@requireUser
def postCommitFixed():
    """本地导出成功后,按预占时锁定的固定价结算。

    ---
    tags: [billing]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [billId]
            properties:
              billId: {type: string}
    responses:
      200:
        description: 结算成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                billId: {type: string}
                realCost: {type: integer}
                balanceAfter: {type: integer}
                refunded: {type: integer, description: 返还的预占金额}
            requestId: {type: string}
      403:
        description: 不能操作其他用户的账单(FORBIDDEN)
      404:
        description: 账单不存在(BILL_NOT_FOUND)
      409:
        description: 账单状态不允许结算(PRICING_RULE_INVALID / BILL_ALREADY_SETTLED)
    """
    try:
        payload = RefundRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        _ownedBill(db, payload.billId)
        result = getBillingService().settleFixed(db, payload.billId)
        return successEnvelope(result.model_dump())


@bp.post("/commit-metered")
@requireUser
def postCommitMetered():
    """下载或导出成功后,按预占时锁定的资源量与价格结算。

    ---
    tags: [billing]
    security:
      - bearerAuth: []
    requestBody:
      required: true
      content:
        application/json:
          schema:
            type: object
            required: [billId]
            properties:
              billId: {type: string}
    responses:
      200:
        description: 结算成功
        schema:
          type: object
          properties:
            code: {type: string, example: OK}
            data:
              type: object
              properties:
                billId: {type: string}
                realCost: {type: integer}
                balanceAfter: {type: integer}
                refunded: {type: integer, description: 返还的预占金额}
            requestId: {type: string}
      403:
        description: 不能操作其他用户的账单(FORBIDDEN)
      404:
        description: 账单不存在(BILL_NOT_FOUND)
      409:
        description: 账单状态不允许结算(PRICING_RULE_INVALID / BILL_ALREADY_SETTLED)
    """
    try:
        payload = RefundRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        _ownedBill(db, payload.billId)
        result = getBillingService().settleMetered(db, payload.billId)
        return successEnvelope(result.model_dump())
