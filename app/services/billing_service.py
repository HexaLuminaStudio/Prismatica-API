# coding: utf-8
"""计费服务:estimate / preauth / settle / refund。

对齐客户端 `BillingService` 行为,server-authoritative:
    - estimate:返回 CostPreview(只读,不修改状态)
    - preauth:余额预占 + 创建 pending bill(幂等键唯一约束)
    - settle:实际结算(realCost <= estimatedCost 时返还差额)
    - refund:全额返还冻结余额

所有写操作通过 `with_for_update()` 行锁 user_balances,防止并发超扣。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import Bill, UserAccount, UserBalance
from app.schemas.billing import (
    CostPreview,
    PreauthResponse,
    RefundResponse,
    SettleResponse,
)
from app.services.pricing import PricingService, getPricingService


class BillingService:
    """计费门面。"""

    def __init__(self, pricing: Optional[PricingService] = None):
        self._pricing = pricing or getPricingService()

    # ---------- Estimate ----------
    def estimate(
        self,
        db: Session,
        userId: str,
        actionType: str,
        resourceUsed: int,
    ) -> CostPreview:
        balance = self._getBalanceRow(db, userId)
        return self._pricing.preview(actionType, resourceUsed, balance.balance)

    # ---------- Preauth ----------
    def preauth(
        self,
        db: Session,
        userId: str,
        actionType: str,
        resourceUsed: int,
        taskId: str = "",
        description: str = "",
        idempotencyKey: Optional[str] = None,
    ) -> PreauthResponse:
        rule = self._pricing.rule(actionType)
        estimatedCost = self._pricing.estimate(actionType, resourceUsed)

        # 行锁 + 幂等键去重
        bill = self._findBillByIdempotency(db, idempotencyKey) if idempotencyKey else None
        if bill is not None:
            return PreauthResponse(
                billId=bill.billId,
                estimatedCost=bill.estimatedCost,
                balanceAfter=bill.balanceAfter,
            )

        balance = self._lockBalance(db, userId)
        if balance.balance < estimatedCost:
            raise ApiError(
                "INSUFFICIENT_BALANCE",
                f"余额不足: 当前 {balance.balance}, 需要 {estimatedCost}",
                details={"currentBalance": balance.balance, "required": estimatedCost},
            )

        balanceBefore = balance.balance
        balance.balance -= estimatedCost
        balance.frozenBalance += estimatedCost
        balance.version += 1

        billId = str(uuid.uuid4())
        bill = Bill(
            billId=billId,
            userId=userId,
            actionType=actionType,
            actionDisplayName=rule.displayName,
            estimatedCost=estimatedCost,
            realCost=estimatedCost,
            resourceUsed=resourceUsed,
            balanceBefore=balanceBefore,
            balanceAfter=balance.balance,
            status="pending",
            taskId=taskId or "",
            description=description or "",
            idempotencyKey=idempotencyKey,
        )
        try:
            db.add(bill)
            db.flush()
        except IntegrityError:
            db.rollback()
            # 并发提交相同 idempotency_key → 取已存在的
            bill = self._findBillByIdempotency(db, idempotencyKey)
            if bill is None:
                raise
            return PreauthResponse(
                billId=bill.billId,
                estimatedCost=bill.estimatedCost,
                balanceAfter=bill.balanceAfter,
            )

        balanceAfter = balance.balance
        db.commit()
        logger.info(
            f"[Billing] preauth user={userId} action={actionType} "
            f"cost={estimatedCost} bill={billId}"
        )
        return PreauthResponse(
            billId=billId,
            estimatedCost=estimatedCost,
            balanceAfter=balanceAfter,
        )

    # ---------- Settle ----------
    def settle(
        self,
        db: Session,
        billId: str,
        realCost: int,
        resourceUsed: int = 0,
    ) -> SettleResponse:
        bill = db.get(Bill, billId)
        if bill is None:
            raise ApiError("BILL_NOT_FOUND", "账单不存在")
        if bill.status == "settled":
            raise ApiError("BILL_ALREADY_SETTLED", "账单已结算")
        if bill.status != "pending":
            raise ApiError("BILL_NOT_PENDING", "账单不在待结算状态")

        realCost = max(0, min(realCost, bill.estimatedCost))
        balance = self._lockBalance(db, bill.userId)
        refundAmount = bill.estimatedCost - realCost
        # 解除冻结 + 写实扣
        balance.frozenBalance -= bill.estimatedCost
        if refundAmount > 0:
            balance.balance += refundAmount
        balance.totalSpent += realCost
        balance.version += 1
        newBalanceAfter = balance.balance

        bill.realCost = realCost
        bill.resourceUsed = resourceUsed or bill.resourceUsed
        bill.balanceAfter = newBalanceAfter
        bill.status = "settled"
        bill.settledAt = datetime.now(timezone.utc).replace(tzinfo=None)

        db.commit()
        logger.info(
            f"[Billing] settle bill={billId} realCost={realCost} "
            f"refund={refundAmount} balanceAfter={newBalanceAfter}"
        )
        return SettleResponse(
            billId=billId,
            realCost=realCost,
            balanceAfter=newBalanceAfter,
            refunded=refundAmount,
        )

    # ---------- Refund ----------
    def refund(self, db: Session, billId: str) -> RefundResponse:
        bill = db.get(Bill, billId)
        if bill is None:
            raise ApiError("BILL_NOT_FOUND", "账单不存在")
        if bill.status == "settled":
            raise ApiError("BILL_ALREADY_SETTLED", "账单已结算,无法退款")
        if bill.status != "pending":
            raise ApiError("BILL_NOT_PENDING", "账单不在待结算状态")

        balance = self._lockBalance(db, bill.userId)
        balance.frozenBalance -= bill.estimatedCost
        balance.balance += bill.estimatedCost
        balance.version += 1
        newBalanceAfter = balance.balance

        bill.status = "refunded"
        bill.balanceAfter = newBalanceAfter
        bill.settledAt = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        logger.info(
            f"[Billing] refund bill={billId} amount={bill.estimatedCost} "
            f"balanceAfter={newBalanceAfter}"
        )
        return RefundResponse(
            billId=billId,
            refundedAmount=bill.estimatedCost,
            balanceAfter=newBalanceAfter,
        )

    # ---------- 内部 ----------
    def _getBalanceRow(self, db: Session, userId: str) -> UserBalance:
        user = db.get(UserAccount, userId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        balance = db.get(UserBalance, userId)
        if balance is None:
            balance = UserBalance(userId=userId)
            db.add(balance)
            db.flush()
        return balance

    def _lockBalance(self, db: Session, userId: str) -> UserBalance:
        """行锁 user_balances(SELECT ... FOR UPDATE)。"""
        balance = db.execute(
            select(UserBalance).where(UserBalance.userId == userId).with_for_update()
        ).scalar_one_or_none()
        if balance is None:
            raise ApiError("NOT_FOUND", "用户余额不存在,请先激活")
        return balance

    def _findBillByIdempotency(
        self, db: Session, key: Optional[str]
    ) -> Optional[Bill]:
        if not key:
            return None
        return db.execute(
            select(Bill).where(Bill.idempotencyKey == key)
        ).scalar_one_or_none()


_billingSingleton: Optional[BillingService] = None


def getBillingService() -> BillingService:
    """全局单例。"""
    global _billingSingleton
    if _billingSingleton is None:
        _billingSingleton = BillingService()
    return _billingSingleton


__all__ = ["BillingService", "getBillingService"]