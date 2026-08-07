"""P0-A cron 脚本集成测试。

覆盖:
    - cron_subscriptions:过期订阅 → expired;应续期订阅 → 派发
    - cron_preauth_release:超 5 分钟的 pending bill → 自动 refund
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
import uuid as _uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.bill import Bill
from app.models.identity import User as IdentityUser, IdentityBalance
from app.services.subscription_service import createSubscription, PLAN_PRO_MONTHLY


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def dbCtx() -> Iterator[dict]:
    """提供 sqlite in-memory + 让 app.db.getDb 走这个引擎。

    cron 脚本在函数内 import `from app.db import getDb`,monkeypatch
    `app.db.getDb` 在 import 之后也会被覆盖(因为 getDb 是在函数体内
    引用的),所以这个 fixture 同时能驱动 cron_subscriptions 和
    cron_preauth_release。
    """
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def _ctx():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    import app.db as appdb

    original = appdb.getDb
    appdb.getDb = _ctx
    try:
        yield {"engine": engine, "factory": factory}
    finally:
        appdb.getDb = original
        engine.dispose()


def _makeUser(factory, balance: int = 500) -> IdentityUser:
    s = factory()
    u = IdentityUser(
        email=f"cron-{_uuid.uuid4().hex[:8]}@example.com",
        passwordHash="x",
        displayName="U",
        tier="free",
        status="active",
    )
    s.add(u)
    s.flush()
    s.add(IdentityBalance(userId=str(u.id), balance=balance, reserved=0))
    s.commit()
    s.refresh(u)
    s.close()
    return u


# ---------------------------------------------------------------------------
# cron_subscriptions
# ---------------------------------------------------------------------------


def test_cron_subscriptions_renews_due(dbCtx) -> None:
    from scripts.cron_subscriptions import runOnce

    user = _makeUser(dbCtx["factory"], balance=200)
    factory = dbCtx["factory"]
    userId = user.id
    with factory() as s:
        sub, _ = createSubscription(s, userId, PLAN_PRO_MONTHLY)
        sub.nextGrantAt = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        sub.currentPeriodEnd = sub.nextGrantAt
        s.commit()

    stats = runOnce(dryRun=False)
    assert stats["expiring"] >= 1

    with factory() as s:
        bal = s.execute(
            select(IdentityBalance).where(IdentityBalance.userId == str(userId))
        ).scalar_one()
        # 初始 200,createSubscription 派发 +200(写 ledger),续期再 +200 = 600
        assert bal.balance == 600


def test_cron_subscriptions_expires_past(dbCtx) -> None:
    from scripts.cron_subscriptions import runOnce

    user = _makeUser(dbCtx["factory"], balance=200)
    factory = dbCtx["factory"]
    userId = user.id
    with factory() as s:
        sub, _ = createSubscription(s, userId, PLAN_PRO_MONTHLY)
        sub.expiresAt = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=1)
        s.commit()
        targetUser = s.get(IdentityUser, userId)
        targetUser.tier = "pro"
        s.commit()

    stats = runOnce(dryRun=False)
    assert stats["expired"] >= 1

    with factory() as s:
        targetUser = s.get(IdentityUser, userId)
        assert targetUser.tier == "free"


# ---------------------------------------------------------------------------
# cron_preauth_release
# ---------------------------------------------------------------------------


def test_cron_preauth_release_refunds_old_pending(dbCtx) -> None:
    from scripts.cron_preauth_release import runOnce
    from app.services.billing_service import preauth

    user = _makeUser(dbCtx["factory"], balance=200)
    factory = dbCtx["factory"]
    with factory() as s:
        preauthResp = preauth(s, user.id, "kwic_search", 1000)
        bill = s.get(Bill, preauthResp.billId)
        bill.createdAt = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10)
        s.commit()

    stats = runOnce(olderThanMinutes=5, dryRun=False)
    assert stats["released"] >= 1

    with factory() as s:
        bill = s.get(Bill, preauthResp.billId)
        assert bill.status == "refunded"
