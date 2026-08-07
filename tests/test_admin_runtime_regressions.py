"""整型用户/兑换码模型迁移后的管理端查询回归测试。"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Bill, LicenseCode, UserAccount, UserBalance
from app.services import admin_bill_service, admin_export_service


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = Session()

    @contextmanager
    def fakeGetDb():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr(admin_bill_service, "getDb", fakeGetDb)
    monkeypatch.setattr(admin_export_service, "getDb", fakeGetDb)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def testAdminBillAndExportsUseCanonicalModelFields(db) -> None:
    user = UserAccount(
        email="runtime-regression@example.com",
        passwordHash="not-used",
        displayName="runtime-user",
        tier="pro",
        status="active",
    )
    db.add(user)
    db.flush()
    db.add(UserBalance(userId=user.id, balance=120, lifetimeGrant=120))
    db.add(
        Bill(
            billId="bill-runtime-regression",
            userId=user.id,
            feature="image",
            estimatedCost=10,
            actualCost=8,
            status="settled",
            idempotencyKey="runtime-regression",
            requestHash="b" * 64,
            preauthExpiresAt=datetime.utcnow() + timedelta(minutes=15),
        )
    )
    db.add(
        LicenseCode(
            codeHash="a" * 64,
            codeKind="INV",
            status="active",
            planCode="pro",
            periodMonths=1,
            monthlyQuota=100,
            maxUses=1,
            usedCount=0,
            expiresAt=datetime.utcnow() + timedelta(days=30),
        )
    )
    db.commit()

    bills, _cursor = admin_bill_service.listBills(limit=5)
    assert bills[0]["userId"] == str(user.id)
    assert bills[0]["displayName"] == "runtime-user"

    usersCsv = admin_export_service.exportUsers(limit=5)
    assert usersCsv[0]["userId"] == str(user.id)
    assert usersCsv[0]["balance"] == 120

    codesCsv = admin_export_service.exportCodes(limit=5)
    assert codesCsv[0]["codeKind"] == "invite"
    assert codesCsv[0]["grantedBalance"] == 100

    billsCsv = admin_export_service.exportBills(limit=5)
    assert billsCsv[0]["userId"] == str(user.id)
    assert billsCsv[0]["displayName"] == "runtime-user"
