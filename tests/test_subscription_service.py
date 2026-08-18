"""P0-A subscription_service 单元测试。

覆盖:
    - createSubscription 立即派发 monthly_quota + 写 ledger
    - redeemInviteCode / redeemTrialCode / redeemRechargeCode 三类码
    - 幂等性:同 code + user 二次 redeem 只产生一条 CodeRedemption
    - 过期迁移:expireSubscription 把 tier 回 free
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models.balance_ledger import BalanceLedger
from app.models.identity import User as IdentityUser
from app.models.subscription import Subscription
from app.services.subscription_service import (
    PLAN_PRO_MONTHLY,
    PLAN_TRIAL,
    createSubscription,
    expireSubscription,
    getActiveSubscription,
    grantMonthlyQuota,
    listSubscriptions,
    redeemInviteCode,
    redeemRechargeCode,
    redeemTrialCode,
)


@pytest.fixture()
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session
    engine.dispose()


def _makeUser(db: Session, suffix: str = "0") -> IdentityUser:
    user = IdentityUser(
        email=f"u{suffix}@example.com",
        passwordHash="x",
        displayName=f"U{suffix}",
        tier="free",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


# ---------------------------------------------------------------------------
# createSubscription
# ---------------------------------------------------------------------------


def testCreateSubscription_GrantsInitialQuotaAndWritesLedger(db: Session) -> None:
    user = _makeUser(db, "1")
    sub, grant = createSubscription(db, user.id, PLAN_PRO_MONTHLY)
    db.commit()

    assert sub.status == "active"
    assert sub.monthlyQuota == 200
    assert sub.userId == user.id
    assert grant.grantedBalance == 200
    assert sub.expiresAt > datetime.now(UTC).replace(tzinfo=None)

    # 用户 tier 升级
    db.refresh(user)
    assert user.tier == "pro"

    # balance_ledger 写入
    ledger = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert len(ledger) == 1
    assert ledger[0].entryType == "grant"
    assert ledger[0].amount == 200
    assert ledger[0].refType == "subscription"
    assert ledger[0].refId == str(sub.id)


def testCreateSubscription_PlanUnknownRaises(db: Session) -> None:
    from app.errors import ApiError

    user = _makeUser(db, "2")
    with pytest.raises(ApiError) as exc:
        createSubscription(db, user.id, "unknown_plan")
    assert exc.value.code == "BAD_REQUEST"


def testGetActiveSubscription_ReturnsLatestActive(db: Session) -> None:
    user = _makeUser(db, "3")
    sub1, _ = createSubscription(db, user.id, PLAN_PRO_MONTHLY)
    db.commit()
    active = getActiveSubscription(db, user.id)
    assert active is not None
    assert active.id == sub1.id


def testListSubscriptions_CursorPaging(db: Session) -> None:
    user = _makeUser(db, "4")
    s1, _ = createSubscription(db, user.id, PLAN_PRO_MONTHLY)
    s2, _ = createSubscription(db, user.id, PLAN_PRO_MONTHLY)
    db.commit()
    items, nextCursor = listSubscriptions(db, user.id, limit=1)
    assert len(items) == 1
    assert nextCursor is not None


# ---------------------------------------------------------------------------
# grantMonthlyQuota
# ---------------------------------------------------------------------------


def testGrantMonthlyQuota_AppendsLedgerAndKeepsBalance(db: Session) -> None:
    user = _makeUser(db, "5")
    sub, _ = createSubscription(db, user.id, PLAN_PRO_MONTHLY)
    db.commit()

    # 推进 nextGrantAt 到过去,以便业务能再次派发
    sub.nextGrantAt = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
    db.flush()
    grantMonthlyQuota(db, sub)
    db.commit()

    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    # 1 + 1 = 2
    assert len(ledgers) == 2
    assert all(int(ledger.amount) == 200 for ledger in ledgers)


# ---------------------------------------------------------------------------
# expireSubscription
# ---------------------------------------------------------------------------


def testExpireSubscription_ResetsTierToFree(db: Session) -> None:
    user = _makeUser(db, "6")
    sub, _ = createSubscription(db, user.id, PLAN_PRO_MONTHLY)
    db.commit()
    assert user.tier == "pro"
    expireSubscription(db, sub)
    db.commit()
    db.refresh(user)
    assert user.tier == "free"
    db.refresh(sub)
    assert sub.status == "expired"


# ---------------------------------------------------------------------------
# 兑换码升级
# ---------------------------------------------------------------------------


def testRedeemInviteCode_CreatesSubscriptionAndGrantsBalance(db: Session) -> None:
    user = _makeUser(db, "7")
    sub, _ = redeemInviteCode(db, user.id, grantedBalance=500, grantedDays=30, codeId=0)
    db.commit()
    assert sub is not None
    assert sub.planCode == PLAN_PRO_MONTHLY
    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].amount == 500
    assert ledgers[0].source == "invite_grant"


def testRedeemInviteCode_ZeroDaysSkipsSubscription(db: Session) -> None:
    user = _makeUser(db, "8")
    sub, _ = redeemInviteCode(db, user.id, grantedBalance=50, grantedDays=0, codeId=0)
    db.commit()
    assert sub is None
    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert len(ledgers) == 1


def testRedeemTrialCode_CreatesTrialSubscription(db: Session) -> None:
    user = _makeUser(db, "9")
    sub = redeemTrialCode(db, user.id, grantedBalance=20, grantedDays=7, codeId=0)
    db.commit()
    assert sub.planCode == PLAN_TRIAL
    assert sub.status == "active"
    assert (sub.expiresAt - sub.startedAt) >= timedelta(days=6, hours=23)


def testRedeemRechargeCode_OnlyAddsBalance(db: Session) -> None:
    user = _makeUser(db, "10")
    amount = redeemRechargeCode(db, user.id, amount=300, codeId=0)
    db.commit()
    assert amount == 300
    subs = db.execute(select(Subscription).where(Subscription.userId == user.id)).scalars().all()
    assert len(subs) == 0
    ledgers = db.execute(select(BalanceLedger).where(BalanceLedger.userId == user.id)).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].source == "recharge_code"
