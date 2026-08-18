"""P0-A billing_service 单元测试。

覆盖:
    - estimate / preauth / settle / refund 全链路
    - ledger 写入:每次 preauth/settle/refund 都产生对应 entry
    - Idempotency-Key 命中 / 不同 body 报错
    - settle 校验 0 <= realCost <= estimated
    - 余额不足 → INSUFFICIENT_BALANCE
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.errors import ApiError
from app.models.balance_ledger import BalanceLedger
from app.models.bill import Bill
from app.models.idempotency_key import IdempotencyKey
from app.models.identity import User as IdentityUser
from app.models.pricing import PricingRuleRecord, PricingVersion
from app.services.billing_service import (
    estimate,
    preauth,
    refund,
    releaseExpiredPreauths,
    settle,
    settleFixed,
    settleMetered,
    settleTokens,
)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session
    engine.dispose()


def _makeUserWithBalance(db: Session, balance: int) -> IdentityUser:
    import uuid as _uuid

    user = IdentityUser(
        email=f"u{_uuid.uuid4().hex[:8]}@example.com",
        passwordHash="x",
        displayName="U",
        tier="free",
        status="active",
    )
    db.add(user)
    db.flush()
    # 直接写 IdentityBalance 行
    from app.models.identity import IdentityBalance

    bal = IdentityBalance(userId=int(user.id), balance=balance, reserved=0)
    db.add(bal)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# estimate
# ---------------------------------------------------------------------------


def testEstimate_ReturnsAffordability(db: Session) -> None:
    user = _makeUserWithBalance(db, 100)
    preview = estimate(db, user.id, "analysis_export", 5000)
    assert preview.actionType == "analysis_export"
    assert preview.estimatedCost == 5
    assert preview.currentBalance == 100
    assert preview.affordable is True


def testEstimate_UnknownActionIsRejected(db: Session) -> None:
    user = _makeUserWithBalance(db, 100)
    with pytest.raises(ApiError) as exc:
        estimate(db, user.id, "unknown_action", 1)
    assert exc.value.code == "PRICING_RULE_NOT_FOUND"


# ---------------------------------------------------------------------------
# preauth + ledger
# ---------------------------------------------------------------------------


def testPreauth_DeductsBalanceAndReservesAndWritesLedger(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    result = preauth(db, user.id, "analysis_export", 1000, taskId="t1", description="测试")
    assert result.estimatedCost > 0
    assert result.balanceAfter == 500 - result.estimatedCost

    # ledger 应该有 1 条 reserve
    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].entryType == "reserve"
    assert ledgers[0].refType == "bill"
    assert ledgers[0].refId == result.billId

    # bill 存在
    bill = db.execute(select(Bill).where(Bill.billId == result.billId)).scalar_one()
    assert bill is not None
    assert bill.status == "pending"
    assert bill.idempotencyKey.startswith("auto:")


def testPreauth_InsufficientBalanceRaises(db: Session) -> None:
    user = _makeUserWithBalance(db, 1)
    with pytest.raises(ApiError) as exc:
        preauth(db, user.id, "analysis_export", 50000)
    assert exc.value.code == "INSUFFICIENT_BALANCE"


def testPreauth_ReleasesExpiredReservationBeforeCheckingBalance(db: Session) -> None:
    user = _makeUserWithBalance(db, 5)
    first = preauth(db, user.id, "analysis_export", 1000, taskId="first")
    bill = db.execute(select(Bill).where(Bill.billId == first.billId)).scalar_one()
    bill.preauthExpiresAt = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()

    second = preauth(db, user.id, "analysis_export", 1000, taskId="second")

    db.refresh(bill)
    assert bill.status == "refunded"
    assert second.estimatedCost == 5


def testReleaseExpiredPreauths_DoesNotReleaseUnexpiredReservation(db: Session) -> None:
    user = _makeUserWithBalance(db, 100)
    result = preauth(db, user.id, "analysis_export", 1000)
    bill = db.execute(select(Bill).where(Bill.billId == result.billId)).scalar_one()

    assert releaseExpiredPreauths(db, userId=user.id) == 0
    assert bill.status == "pending"


# ---------------------------------------------------------------------------
# Idempotency-Key
# ---------------------------------------------------------------------------


def testPreauth_IdempotencyKeyReturnsSameBill(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    r1 = preauth(db, user.id, "analysis_export", 1000, taskId="t1", idempotencyKey="key-001")
    db.commit()

    r2 = preauth(db, user.id, "analysis_export", 1000, taskId="t1", idempotencyKey="key-001")
    # r2 应该复用 r1 的 bill_id
    assert r2.billId == r1.billId

    # idempotency_keys 表应该只有 1 行
    idem = db.execute(select(IdempotencyKey)).scalars().all()
    assert len(idem) == 1
    assert idem[0].idempotencyKey == "key-001"


def testPreauth_IdempotencyKeyBodyMismatchRaises409(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauth(db, user.id, "analysis_export", 1000, taskId="t1", idempotencyKey="key-002")
    db.commit()
    with pytest.raises(ApiError) as exc:
        preauth(db, user.id, "analysis_export", 1000, taskId="t2", idempotencyKey="key-002")
    # 2026-08-07 P0-A M9 错误码语义化:统一为 IDEMPOTENCY_CONFLICT(原 CONFLICT)
    assert exc.value.code == "IDEMPOTENCY_CONFLICT"


# ---------------------------------------------------------------------------
# settle
# ---------------------------------------------------------------------------


def testSettle_FullSettleConsumesAndReleasesReserve(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "analysis_export", 1000)
    db.commit()
    estimated = preauthResp.estimatedCost
    500 - estimated  # 全额结算不退还
    settle(db, preauthResp.billId, realCost=estimated)
    db.commit()

    bill = db.execute(select(Bill).where(Bill.billId == preauthResp.billId)).scalar_one()
    assert bill.status == "settled"
    assert bill.actualCost == estimated

    # ledger 应该:1 reserve + 1 consume
    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert {ledger.entryType for ledger in ledgers} == {"reserve", "consume"}


def testSettle_PartialSettleRefundsDifference(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "analysis_export", 5000)
    db.commit()
    estimated = preauthResp.estimatedCost
    realCost = max(1, estimated - 2)
    result = settle(db, preauthResp.billId, realCost=realCost)
    db.commit()
    assert result.refunded == estimated - realCost
    # ledger 应该有 reserve + unreserve + (consume if realCost > 0)
    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert {ledger.entryType for ledger in ledgers} == {"reserve", "unreserve", "consume"}


def testSettle_RealCostOutOfRangeRaises(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "analysis_export", 1000)
    db.commit()
    with pytest.raises(ApiError) as exc:
        settle(db, preauthResp.billId, realCost=preauthResp.estimatedCost + 1)
    assert exc.value.code == "BAD_REQUEST"
    with pytest.raises(ApiError) as exc:
        settle(db, preauthResp.billId, realCost=-1)
    assert exc.value.code == "BAD_REQUEST"


def testSettle_NotPendingRaises(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "analysis_export", 1000)
    db.commit()
    settle(db, preauthResp.billId, realCost=preauthResp.estimatedCost)
    db.commit()
    with pytest.raises(ApiError) as exc:
        settle(db, preauthResp.billId, realCost=1)
    assert exc.value.code == "BILL_ALREADY_SETTLED"


def testSettle_ExpiredPreauthReleasesReservation(db: Session) -> None:
    user = _makeUserWithBalance(db, 100)
    result = preauth(db, user.id, "analysis_export", 1000)
    bill = db.execute(select(Bill).where(Bill.billId == result.billId)).scalar_one()
    bill.preauthExpiresAt = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(ApiError) as captured:
        settle(db, result.billId, realCost=result.estimatedCost)

    assert captured.value.code == "BILL_NOT_PENDING"
    db.refresh(bill)
    assert bill.status == "refunded"


def testSettleFixed_IsIdempotentForClientRetry(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "analysis_export", 1)
    first = settleFixed(db, preauthResp.billId)
    second = settleFixed(db, preauthResp.billId)
    assert first.realCost == second.realCost == 5
    assert first.balanceAfter == second.balanceAfter == 495


def testSettleMetered_UsesLockedResourceAndPriceSnapshot(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "hsk_download", 1_001)
    bill = db.execute(select(Bill).where(Bill.billId == preauthResp.billId)).scalar_one()
    assert bill.pricingSnapshot["quotedResourceUsed"] == 1_001
    assert preauthResp.estimatedCost == 6

    first = settleMetered(db, preauthResp.billId)
    second = settleMetered(db, preauthResp.billId)
    assert first.realCost == second.realCost == 6
    assert first.balanceAfter == second.balanceAfter == 494


def testSettleTokens_KeepsPreauthPriceSnapshotAfterPublish(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(
        db,
        user.id,
        "ai_chat",
        0,
        estimatedInputTokens=2_000,
        estimatedOutputTokens=2_000,
    )
    assert preauthResp.pricingVersion == "2026.08.17-affordable-ai"

    version = PricingVersion(
        versionCode="test-new-price",
        status="published",
        note="test",
        createdBy="test",
        publishedBy="test",
        publishedAt=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(version)
    db.flush()
    db.add(
        PricingRuleRecord(
            versionId=version.versionId,
            featureCode="ai_chat",
            displayName="AI 聊天",
            billingMode="token",
            unitName="Token",
            unitSize=1_000_000,
            inputTokenCostPer1K=50,
            outputTokenCostPer1K=80,
            minCost=1,
            maxCost=1_000_000,
            enabled=True,
            ruleMeta={"tokenPricingVersion": 2},
        )
    )
    db.commit()

    result = settleTokens(db, preauthResp.billId, inputTokens=100, outputTokens=100)
    assert result.realCost == 1
    bill = db.execute(select(Bill).where(Bill.billId == preauthResp.billId)).scalar_one()
    assert bill.pricingVersion == "2026.08.17-affordable-ai"
    assert bill.inputTokens == 100
    assert bill.outputTokens == 100


# ---------------------------------------------------------------------------
# refund
# ---------------------------------------------------------------------------


def testRefund_ReturnsAllBalanceAndReleasesReserve(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "analysis_export", 1000)
    db.commit()
    refund(db, preauthResp.billId)
    db.commit()

    bill = db.execute(select(Bill).where(Bill.billId == preauthResp.billId)).scalar_one()
    assert bill.status == "refunded"
    # 用户余额应该恢复到 500
    from app.models.identity import IdentityBalance

    bal = db.execute(select(IdentityBalance).where(IdentityBalance.userId == str(user.id))).scalar_one()
    assert bal.balance == 500
    assert bal.reserved == 0

    # ledger:reserve + refund
    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert {ledger.entryType for ledger in ledgers} == {"reserve", "refund"}


def testRefund_AlreadySettledRaises(db: Session) -> None:
    user = _makeUserWithBalance(db, 500)
    preauthResp = preauth(db, user.id, "analysis_export", 1000)
    db.commit()
    settle(db, preauthResp.billId, realCost=preauthResp.estimatedCost)
    db.commit()
    with pytest.raises(ApiError) as exc:
        refund(db, preauthResp.billId)
    assert exc.value.code == "BILL_ALREADY_SETTLED"
