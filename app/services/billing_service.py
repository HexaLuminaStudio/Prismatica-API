"""P0-A 计费服务:estimate / preauth / settle / refund(加固版)。

2026-08-07 重构要点:
    1) 所有写操作走单事务:UPDATE user_balance + INSERT balance_ledger 在同一 DB session。
       balance_ledger 不可变,只追加,便于审计。
    2) preauth: balance -= cost, reserved += cost, bill.status='pending',写 ledger(reserve)。
    3) settle: 解除预占(reserved -= estimated),balance -= actual;
       若 estimated > actual 差额退回 balance,写两条 ledger(consume + unreserve)。
    4) refund: reserved -= cost, balance += cost(全退),bill.status='refunded'。
    5) Idempotency-Key 写到 idempotency_keys 表,24h 窗口;key+body_hash 命中直接返回原响应。
    6) settle 校验 0 <= actual_cost <= estimated;违反 → 400。

并发安全:
    - 所有写操作通过 `with_for_update()` 行锁 user_balance
    - CHECK 约束(余额 >= 0 / reserved <= balance)兜底,DB 报错时 service 转 500

userId 类型: BIGINT(对齐 P0-A IdentityUser.id)。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models.balance_ledger import BalanceLedger
from app.models.bill import Bill
from app.models.idempotency_key import IdempotencyKey
from app.models.identity import IdentityBalance
from app.schemas.billing import (
    CostPreview,
    PreauthResponse,
    RefundResponse,
    SettleResponse,
)
from app.services.pricing import PricingService, costFromSnapshot, getPricingService

IDEMPOTENCY_WINDOW_HOURS = 24


@dataclass(frozen=True)
class _IdempotencyHit:
    """幂等键命中:直接复用原响应(避免重复扣费)。"""

    responseStatus: int
    responseBody: dict[str, Any]


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hashRequest(payload: dict[str, Any]) -> str:
    """计算请求体 SHA-256(供 idempotency 校验:同一 key + 不同 body 视为冲突)。"""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _userIdAsInt(userId: int) -> int:
    return int(userId)


def _ensureIdentityBalance(db: Session, userId: int) -> IdentityBalance:
    """确保 user_balance 行存在(若不存在则创建空余额)。"""
    numericUserId = _userIdAsInt(userId)
    balance = db.execute(
        select(IdentityBalance).where(IdentityBalance.userId == numericUserId)
    ).scalar_one_or_none()
    if balance is None:
        balance = IdentityBalance(userId=numericUserId)
        db.add(balance)
        db.flush()
    return balance


def _lockIdentityBalance(db: Session, userId: int) -> IdentityBalance:
    """行锁 user_balance(SELECT ... FOR UPDATE)。"""
    numericUserId = _userIdAsInt(userId)
    balance = db.execute(
        select(IdentityBalance)
        .where(IdentityBalance.userId == numericUserId)
        .with_for_update()
    ).scalar_one_or_none()
    if balance is None:
        raise ApiError("NOT_FOUND", "用户余额不存在,请先激活", httpStatus=404)
    return balance


def _writeLedger(
    db: Session,
    *,
    userId: int,
    entryType: str,
    amount: int,
    balanceDelta: int,
    reservedDelta: int,
    balanceAfter: int,
    reservedAfter: int,
    source: str,
    refType: str | None = None,
    refId: str | None = None,
    note: str = "",
) -> None:
    db.add(
        BalanceLedger(
            userId=userId,
            entryType=entryType,
            amount=amount,
            balanceDelta=balanceDelta,
            reservedDelta=reservedDelta,
            balanceAfter=balanceAfter,
            reservedAfter=reservedAfter,
            source=source,
            refType=refType,
            refId=refId,
            note=note,
        )
    )
    db.flush()


# ---------------------------------------------------------------------------
# 幂等键处理
# ---------------------------------------------------------------------------


def _findIdempotencyHit(
    db: Session,
    *,
    userId: int,
    operation: str,
    idempotencyKey: str,
    requestHash: str,
) -> _IdempotencyHit | None:
    """查 idempotency_keys 表。返回 None 表示未命中;返回 hit 表示可复用原响应。

    命中条件:同 user + operation + key,且 request_hash 相同;否则视为冲突报错。
    """
    row = db.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.userId == userId,
            IdempotencyKey.operation == operation,
            IdempotencyKey.idempotencyKey == idempotencyKey,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.expiresAt <= _now():
        return None
    if row.requestHash != requestHash:
        raise ApiError(
            "IDEMPOTENCY_CONFLICT",
            "idempotency_key 与历史请求不匹配(body hash 不同)",
            httpStatus=409,
        )
    if row.responseStatus is None or row.responseBody is None:
        return None
    return _IdempotencyHit(
        responseStatus=int(row.responseStatus),
        responseBody=dict(row.responseBody),
    )


def _recordIdempotency(
    db: Session,
    *,
    userId: int,
    operation: str,
    idempotencyKey: str,
    requestHash: str,
    responseStatus: int,
    responseBody: dict[str, Any],
    resourceType: str | None = None,
    resourceId: str | None = None,
) -> None:
    expiresAt = _now() + timedelta(hours=IDEMPOTENCY_WINDOW_HOURS)
    try:
        db.add(
            IdempotencyKey(
                userId=userId,
                operation=operation,
                idempotencyKey=idempotencyKey,
                requestHash=requestHash,
                responseStatus=responseStatus,
                responseBody=responseBody,
                resourceType=resourceType,
                resourceId=resourceId,
                expiresAt=expiresAt,
            )
        )
        db.flush()
    except IntegrityError:
        # 唯一约束冲突(同 user/op/key) — 视为已存在,直接吞掉
        db.rollback()


# ---------------------------------------------------------------------------
# 计价(只读,无副作用)
# ---------------------------------------------------------------------------


def estimate(
    db: Session,
    userId: int,
    actionType: str,
    resourceUsed: int,
    pricing: PricingService | None = None,
) -> CostPreview:
    balance = _ensureIdentityBalance(db, userId)
    pricing = pricing or getPricingService()
    availableBalance = int(balance.balance or 0) - int(balance.reserved or 0)
    return pricing.preview(actionType, resourceUsed, availableBalance, db=db)


# ---------------------------------------------------------------------------
# 预占
# ---------------------------------------------------------------------------


def preauth(
    db: Session,
    userId: int,
    actionType: str,
    resourceUsed: int,
    *,
    taskId: str = "",
    description: str = "",
    idempotencyKey: str | None = None,
    operation: str = "billing.preauth",
    pricing: PricingService | None = None,
    estimatedInputTokens: int = 0,
    estimatedOutputTokens: int = 0,
) -> PreauthResponse:
    """预占(冻结)余额;同时写 ledger(reserve)+ idempotency_keys。"""
    pricing = pricing or getPricingService()
    quote = pricing.quote(
        actionType,
        db=db,
        resourceUsed=resourceUsed,
        inputTokens=estimatedInputTokens,
        outputTokens=estimatedOutputTokens,
    )
    estimatedCost = quote.estimatedCost

    requestPayload: dict[str, Any] = {
        "userId": userId,
        "actionType": actionType,
        "resourceUsed": resourceUsed,
        "taskId": taskId,
        "description": description,
        "estimatedInputTokens": estimatedInputTokens,
        "estimatedOutputTokens": estimatedOutputTokens,
    }
    requestHash = _hashRequest(requestPayload)

    if idempotencyKey:
        hit = _findIdempotencyHit(
            db,
            userId=userId,
            operation=operation,
            idempotencyKey=idempotencyKey,
            requestHash=requestHash,
        )
        if hit is not None:
            logger.info(f"[Billing] preauth idempotency hit user={userId} key={idempotencyKey}")
            return PreauthResponse.model_validate(hit.responseBody)

    balance = _lockIdentityBalance(db, userId)
    availableBefore = int(balance.balance or 0) - int(balance.reserved or 0)
    if availableBefore < estimatedCost:
        raise ApiError(
            "INSUFFICIENT_BALANCE",
            f"余额不足: 当前可用 {availableBefore}, 需要 {estimatedCost}",
            details={"currentBalance": availableBefore, "required": estimatedCost},
        )

    balanceBefore = int(balance.balance or 0)
    balance.reserved = int(balance.reserved or 0) + estimatedCost
    balance.version = int(balance.version or 0) + 1
    db.flush()
    reservedAfter = int(balance.reserved or 0)
    availableAfter = int(balance.balance or 0) - reservedAfter

    billId = str(uuid.uuid4())
    bill = Bill(
        billId=billId,
        userId=userId,
        feature=actionType,
        estimatedCost=estimatedCost,
        actualCost=None,
        status="pending",
        description=description or "",
        pricingVersion=quote.pricingVersion,
        pricingSnapshot={**quote.ruleSnapshot, "quotedResourceUsed": max(0, int(resourceUsed))},
        idempotencyKey=idempotencyKey or f"auto:{billId}",
        requestHash=requestHash,
        preauthExpiresAt=_now() + timedelta(minutes=15),
    )
    db.add(bill)
    db.flush()

    _writeLedger(
        db,
        userId=userId,
        entryType="reserve",
        amount=estimatedCost,
        balanceDelta=0,
        reservedDelta=+estimatedCost,
        balanceAfter=balanceBefore,
        reservedAfter=reservedAfter,
        source="preauth",
        refType="bill",
        refId=billId,
        note=f"{actionType} 预占",
    )

    result = PreauthResponse(
        billId=billId,
        estimatedCost=estimatedCost,
        balanceAfter=availableAfter,
        pricingVersion=quote.pricingVersion,
        billingMode=quote.billingMode,
    )

    if idempotencyKey:
        _recordIdempotency(
            db,
            userId=userId,
            operation=operation,
            idempotencyKey=idempotencyKey,
            requestHash=requestHash,
            responseStatus=200,
            responseBody=result.model_dump(mode="json"),
            resourceType="bill",
            resourceId=billId,
        )

    db.commit()
    logger.info(
        f"[Billing] preauth user={userId} action={actionType} "
        f"cost={estimatedCost} bill={billId} availableAfter={availableAfter} reserved={reservedAfter}"
    )
    return result


# ---------------------------------------------------------------------------
# 结算
# ---------------------------------------------------------------------------


def settle(
    db: Session,
    billId: str,
    realCost: int,
    resourceUsed: int = 0,
    *,
    operation: str = "billing.settle",
) -> SettleResponse:
    """结算 bill:reserved -= estimated;balance -= actual;差额退 reserve。"""
    requestPayload = {"billId": billId, "realCost": realCost, "resourceUsed": resourceUsed}
    requestHash = _hashRequest(requestPayload)
    bill = db.execute(select(Bill).where(Bill.billId == billId).with_for_update()).scalar_one_or_none()
    if bill is None:
        raise ApiError("BILL_NOT_FOUND", "账单不存在", httpStatus=409)
    userId = int(bill.userId)

    if bill.status == "settled":
        raise ApiError("BILL_ALREADY_SETTLED", "账单已结算", httpStatus=409)
    if bill.status != "pending":
        raise ApiError("BILL_NOT_PENDING", "账单不在待结算状态", httpStatus=409)

    estimated = int(bill.estimatedCost or 0)
    if realCost < 0 or realCost > estimated:
        raise ApiError(
            "BAD_REQUEST",
            f"actual_cost 必须在 [0, {estimated}] 范围内,实际 {realCost}",
            httpStatus=400,
        )

    balance = _lockIdentityBalance(db, userId)
    reservedBefore = int(balance.reserved or 0)
    balanceBefore = int(balance.balance or 0)

    # 解除整笔预占，并从总余额扣除实际消费。
    balance.reserved = max(0, reservedBefore - estimated)
    refundAmount = estimated - realCost
    balance.balance = balanceBefore - realCost
    balance.lifetimeConsumed = int(balance.lifetimeConsumed or 0) + realCost
    balance.version = int(balance.version or 0) + 1
    db.flush()
    reservedAfter = int(balance.reserved or 0)
    balanceAfter = int(balance.balance or 0)

    bill.actualCost = realCost
    bill.inputTokens = None
    bill.outputTokens = None
    bill.status = "settled"
    bill.settledAt = _now()
    db.flush()

    # 写两条 ledger:consume(realCost) + unreserve(estimated - realCost)
    if realCost > 0:
        _writeLedger(
            db,
            userId=userId,
            entryType="consume",
            amount=realCost,
            balanceDelta=-realCost,
            reservedDelta=-realCost,
            balanceAfter=balanceAfter,
            reservedAfter=reservedAfter,
            source="settle",
            refType="bill",
            refId=billId,
            note=f"{bill.feature} 实付",
        )
    if refundAmount > 0:
        _writeLedger(
            db,
            userId=userId,
            entryType="unreserve",
            amount=refundAmount,
            balanceDelta=0,
            reservedDelta=-refundAmount,
            balanceAfter=balanceAfter,
            reservedAfter=reservedAfter,
            source="settle",
            refType="bill",
            refId=billId,
            note=f"{bill.feature} 释放差额",
        )

    result = SettleResponse(
        billId=billId,
        realCost=realCost,
        balanceAfter=balanceAfter,
        refunded=refundAmount,
    )

    if bill.idempotencyKey and not bill.idempotencyKey.startswith("auto:"):
        _recordIdempotency(
            db,
            userId=userId,
            operation=operation,
            idempotencyKey=bill.idempotencyKey,
            requestHash=requestHash,
            responseStatus=200,
            responseBody=result.model_dump(mode="json"),
            resourceType="bill",
            resourceId=billId,
        )

    db.commit()
    logger.info(
        f"[Billing] settle bill={billId} realCost={realCost} "
        f"refund={refundAmount} balanceAfter={balanceAfter} reserved={reservedAfter}"
    )
    return result


def settleFixed(db: Session, billId: str) -> SettleResponse:
    """固定价任务成功后按账单快照原价结算。"""
    bill = db.execute(select(Bill).where(Bill.billId == billId)).scalar_one_or_none()
    if bill is None:
        raise ApiError("BILL_NOT_FOUND", httpStatus=409)
    snapshot = dict(bill.pricingSnapshot or {})
    if snapshot.get("billingMode") != "fixed":
        raise ApiError("PRICING_RULE_INVALID", "该账单不是固定价任务", httpStatus=409)
    if bill.status == "settled":
        balance = _lockIdentityBalance(db, int(bill.userId))
        actualCost = int(bill.actualCost or 0)
        return SettleResponse(
            billId=bill.billId,
            realCost=actualCost,
            balanceAfter=int(balance.balance or 0) - int(balance.reserved or 0),
            refunded=max(0, int(bill.estimatedCost or 0) - actualCost),
        )
    return settle(db, billId, realCost=int(bill.estimatedCost or 0))


def settleMetered(db: Session, billId: str) -> SettleResponse:
    """按预占时保存的资源量和价格快照结算，客户端不能下调实际费用。"""
    bill = db.execute(select(Bill).where(Bill.billId == billId)).scalar_one_or_none()
    if bill is None:
        raise ApiError("BILL_NOT_FOUND", httpStatus=409)
    snapshot = dict(bill.pricingSnapshot or {})
    if snapshot.get("billingMode") != "metered":
        raise ApiError("PRICING_RULE_INVALID", "该账单不是按量计费任务", httpStatus=409)
    if bill.status == "settled":
        balance = _lockIdentityBalance(db, int(bill.userId))
        actualCost = int(bill.actualCost or 0)
        return SettleResponse(
            billId=bill.billId,
            realCost=actualCost,
            balanceAfter=int(balance.balance or 0) - int(balance.reserved or 0),
            refunded=max(0, int(bill.estimatedCost or 0) - actualCost),
        )
    resourceUsed = max(0, int(snapshot.get("quotedResourceUsed", 0) or 0))
    realCost = costFromSnapshot(snapshot, resourceUsed=resourceUsed)
    if realCost != int(bill.estimatedCost or 0):
        raise ApiError("PRICING_RULE_INVALID", "账单预占金额与价格快照不一致", httpStatus=409)
    return settle(db, billId, realCost=realCost, resourceUsed=resourceUsed)


def settleTokens(db: Session, billId: str, inputTokens: int, outputTokens: int) -> SettleResponse:
    """AI 调用完成后按供应商返回的真实 Token 和锁定快照结算。"""
    bill = db.execute(select(Bill).where(Bill.billId == billId)).scalar_one_or_none()
    if bill is None:
        raise ApiError("BILL_NOT_FOUND", httpStatus=409)
    snapshot = dict(bill.pricingSnapshot or {})
    if snapshot.get("billingMode") != "token":
        raise ApiError("PRICING_RULE_INVALID", "该账单不是 Token 计费任务", httpStatus=409)
    realCost = costFromSnapshot(
        snapshot,
        inputTokens=max(0, int(inputTokens)),
        outputTokens=max(0, int(outputTokens)),
    )
    if realCost > int(bill.estimatedCost or 0):
        realCost = int(bill.estimatedCost or 0)
    result = settle(db, billId, realCost=realCost, resourceUsed=inputTokens + outputTokens)
    refreshedBill = db.execute(select(Bill).where(Bill.billId == billId)).scalar_one()
    refreshedBill.inputTokens = max(0, int(inputTokens))
    refreshedBill.outputTokens = max(0, int(outputTokens))
    db.commit()
    return result


# ---------------------------------------------------------------------------
# 退款
# ---------------------------------------------------------------------------


def refund(
    db: Session,
    billId: str,
    *,
    operation: str = "billing.refund",
) -> RefundResponse:
    """退款:reserved -= cost, balance += cost(全退),bill.status='refunded'。"""
    requestPayload = {"billId": billId}
    requestHash = _hashRequest(requestPayload)
    bill = db.execute(select(Bill).where(Bill.billId == billId).with_for_update()).scalar_one_or_none()
    if bill is None:
        raise ApiError("BILL_NOT_FOUND", "账单不存在", httpStatus=409)
    userId = int(bill.userId)
    if bill.status == "settled":
        raise ApiError("BILL_ALREADY_SETTLED", "账单已结算,无法退款", httpStatus=409)
    if bill.status != "pending":
        raise ApiError("BILL_NOT_PENDING", "账单不在待结算状态", httpStatus=409)

    estimated = int(bill.estimatedCost or 0)
    balance = _lockIdentityBalance(db, userId)
    reservedBefore = int(balance.reserved or 0)
    balance.reserved = max(0, reservedBefore - estimated)
    balance.version = int(balance.version or 0) + 1
    db.flush()
    reservedAfter = int(balance.reserved or 0)
    balanceAfter = int(balance.balance or 0)

    bill.status = "refunded"
    bill.refundedAt = _now()
    db.flush()

    _writeLedger(
        db,
        userId=userId,
        entryType="refund",
        amount=estimated,
        balanceDelta=0,
        reservedDelta=-estimated,
        balanceAfter=balanceAfter,
        reservedAfter=reservedAfter,
        source="refund",
        refType="bill",
        refId=billId,
        note=f"{bill.feature} 退款",
    )

    result = RefundResponse(
        billId=billId,
        refundedAmount=estimated,
        balanceAfter=balanceAfter,
    )

    if bill.idempotencyKey and not bill.idempotencyKey.startswith("auto:"):
        _recordIdempotency(
            db,
            userId=userId,
            operation=operation,
            idempotencyKey=bill.idempotencyKey,
            requestHash=requestHash,
            responseStatus=200,
            responseBody=result.model_dump(mode="json"),
            resourceType="bill",
            resourceId=billId,
        )

    db.commit()
    logger.info(
        f"[Billing] refund bill={billId} amount={estimated} "
        f"balanceAfter={balanceAfter} reserved={reservedAfter}"
    )
    return result


# ---------------------------------------------------------------------------
# 单例
# ---------------------------------------------------------------------------


class BillingService:
    """门面(允许注入 mock 计价规则)。"""

    def __init__(self, pricing: PricingService | None = None):
        self._pricing = pricing or getPricingService()

    def estimate(self, db: Session, userId: int, actionType: str, resourceUsed: int) -> CostPreview:
        return estimate(db, userId, actionType, resourceUsed, self._pricing)

    def preauth(
        self,
        db: Session,
        userId: int,
        actionType: str,
        resourceUsed: int,
        *,
        taskId: str = "",
        description: str = "",
        idempotencyKey: str | None = None,
        estimatedInputTokens: int = 0,
        estimatedOutputTokens: int = 0,
    ) -> PreauthResponse:
        return preauth(
            db,
            userId,
            actionType,
            resourceUsed,
            taskId=taskId,
            description=description,
            idempotencyKey=idempotencyKey,
            pricing=self._pricing,
            estimatedInputTokens=estimatedInputTokens,
            estimatedOutputTokens=estimatedOutputTokens,
        )

    def settle(self, db: Session, billId: str, realCost: int, resourceUsed: int = 0) -> SettleResponse:
        return settle(db, billId, realCost, resourceUsed)

    def refund(self, db: Session, billId: str) -> RefundResponse:
        return refund(db, billId)

    def settleFixed(self, db: Session, billId: str) -> SettleResponse:
        return settleFixed(db, billId)

    def settleMetered(self, db: Session, billId: str) -> SettleResponse:
        return settleMetered(db, billId)

    def settleTokens(self, db: Session, billId: str, inputTokens: int, outputTokens: int) -> SettleResponse:
        return settleTokens(db, billId, inputTokens, outputTokens)


_billingSingleton: BillingService | None = None


def getBillingService() -> BillingService:
    global _billingSingleton
    if _billingSingleton is None:
        _billingSingleton = BillingService()
    return _billingSingleton


# snake_case aliases
estimate_svc = estimate
preauth_svc = preauth
settle_svc = settle
refund_svc = refund


__all__ = [
    "IDEMPOTENCY_WINDOW_HOURS",
    "BillingService",
    "getBillingService",
    "estimate",
    "preauth",
    "settle",
    "refund",
    "settleFixed",
    "settleMetered",
    "settleTokens",
    "estimate_svc",
    "preauth_svc",
    "settle_svc",
    "refund_svc",
]
