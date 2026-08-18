"""整型用户/兑换码模型迁移后的管理端查询回归测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Bill, CodeRedemption, LicenseCode, UserAccount, UserBalance
from app.services import admin_audit_service, admin_bill_service, admin_export_service


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

    monkeypatch.setattr(admin_audit_service, "getDb", fakeGetDb)
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


def testAdminMetricsDashboardDataUsesCanonicalModelFields(db) -> None:
    proUser = UserAccount(
        email="metrics-pro@example.com",
        passwordHash="not-used",
        displayName="metrics-pro",
        tier="pro",
        status="active",
    )
    freeUser = UserAccount(
        email="metrics-free@example.com",
        passwordHash="not-used",
        displayName="metrics-free",
        tier="free",
        status="paused",
    )
    db.add_all([proUser, freeUser])
    db.flush()
    activeCode = LicenseCode(
        codeHash="c" * 64,
        codeKind="INV",
        status="active",
        planCode="pro",
        periodMonths=1,
        monthlyQuota=100,
        maxUses=1,
        usedCount=0,
        issuedAt=datetime.utcnow(),
        expiresAt=datetime.utcnow() + timedelta(days=30),
    )
    revokedCode = LicenseCode(
        codeHash="d" * 64,
        codeKind="TRY",
        status="revoked",
        trialDays=7,
        monthlyQuota=20,
        maxUses=1,
        usedCount=0,
        issuedAt=datetime.utcnow(),
        revokedAt=datetime.utcnow(),
        expiresAt=datetime.utcnow() + timedelta(days=7),
    )
    exhaustedCode = LicenseCode(
        codeHash="e" * 64,
        codeKind="RCH",
        status="exhausted",
        amount=50,
        maxUses=1,
        usedCount=1,
        issuedAt=datetime.utcnow(),
        expiresAt=datetime.utcnow() + timedelta(days=30),
    )
    db.add_all([activeCode, revokedCode, exhaustedCode])
    db.flush()
    db.add(
        CodeRedemption(
            codeId=exhaustedCode.id,
            userId=proUser.id,
            amountGranted=50,
            redeemedAt=datetime.utcnow(),
        )
    )
    db.commit()

    distribution = admin_audit_service.subscriptionDistribution()
    kpi = admin_audit_service.codesKpi()

    assert distribution["total"] == 2
    assert {item["tier"]: item["count"] for item in distribution["items"]} == {"pro": 1, "free": 1}
    assert kpi == {
        "activeCount": 1,
        "consumedLast7Days": 1,
        "issuedLast7Days": 3,
        "revokedLast7Days": 1,
    }
