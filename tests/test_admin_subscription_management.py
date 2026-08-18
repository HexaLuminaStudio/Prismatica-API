"""管理员开通订阅服务回归测试。"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.errors import ApiError
from app.models.identity import User
from app.services import admin_user_service
from app.services.subscription_service import PLAN_PRO_MONTHLY


@pytest.fixture()
def adminSubscriptionContext(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = factory()
    audits = []

    @contextmanager
    def fakeGetDb():
        try:
            yield session
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(admin_user_service, "getDb", fakeGetDb)
    monkeypatch.setattr(
        admin_user_service,
        "recordAudit",
        lambda **payload: audits.append(payload),
    )
    user = User(
        email="admin-subscription@example.com",
        passwordHash="not-used",
        displayName="Admin Subscription",
        tier="free",
        status="active",
    )
    session.add(user)
    session.commit()
    try:
        yield session, user, audits
    finally:
        session.close()
        engine.dispose()


def testCreateUserSubscriptionGrantsPlanAndWritesAudit(
    adminSubscriptionContext,
) -> None:
    session, user, audits = adminSubscriptionContext

    result = admin_user_service.createUserSubscription(
        str(user.id),
        PLAN_PRO_MONTHLY,
    )

    assert result["userId"] == str(user.id)
    assert result["subscription"]["planCode"] == PLAN_PRO_MONTHLY
    assert result["subscription"]["status"] == "active"
    assert result["grantedBalance"] == 200
    session.refresh(user)
    assert user.tier == "pro"
    assert audits[0]["action"] == "admin.create_subscription"


def testCreateUserSubscriptionRejectsSecondActiveSubscription(
    adminSubscriptionContext,
) -> None:
    _session, user, _audits = adminSubscriptionContext
    admin_user_service.createUserSubscription(str(user.id), PLAN_PRO_MONTHLY)

    with pytest.raises(ApiError) as captured:
        admin_user_service.createUserSubscription(str(user.id), PLAN_PRO_MONTHLY)

    assert captured.value.code == "CONFLICT"
    assert captured.value.httpStatus == 409
