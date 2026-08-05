# coding: utf-8
"""/v1/billing/* 路由:estimate / preauth / settle / refund。"""
from __future__ import annotations

from contextlib import contextmanager

from flask import Blueprint, g, jsonify, request
from pydantic import ValidationError

from app.db import getDb
from app.deps import requireAuth
from app.errors import ApiError
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


@bp.post("/estimate")
@requireAuth
def postEstimate():
    try:
        payload = EstimateRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()})

    with _sessionCtx() as db:
        preview = getBillingService().estimate(
            db,
            userId=g.userId,
            actionType=payload.actionType,
            resourceUsed=payload.resourceUsed,
        )
        return jsonify(preview.model_dump())


@bp.post("/preauth")
@requireAuth
def postPreauth():
    try:
        payload = PreauthRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()})

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
        return jsonify(result.model_dump())


@bp.post("/settle")
@requireAuth
def postSettle():
    try:
        payload = SettleRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()})

    with _sessionCtx() as db:
        result = getBillingService().settle(
            db,
            billId=payload.billId,
            realCost=payload.realCost,
            resourceUsed=payload.resourceUsed,
        )
        return jsonify(result.model_dump())


@bp.post("/refund")
@requireAuth
def postRefund():
    try:
        payload = RefundRequest.model_validate(request.get_json(force=True, silent=False))
    except ValidationError as e:
        raise ApiError("BAD_REQUEST", "请求参数错误", details={"errors": e.errors()})

    with _sessionCtx() as db:
        result = getBillingService().refund(db, billId=payload.billId)
        return jsonify(result.model_dump())