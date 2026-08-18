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
    """本地导出成功后，按预占时锁定的固定价结算。"""
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
    """下载或导出成功后，按预占时锁定的资源量与价格结算。"""
    try:
        payload = RefundRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as error:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": error.errors()}) from error
    with _sessionCtx() as db:
        _ownedBill(db, payload.billId)
        result = getBillingService().settleMetered(db, payload.billId)
        return successEnvelope(result.model_dump())
