"""后台用户状态变更必须让既有令牌立即失效。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    BalanceLedger,
    Bill,
    CodeRedemption,
    IdempotencyKey,
    IdentityDevice,
    IdentityUser,
    LicenseCode,
    PasswordResetToken,
    RechargeRecord,
    RevokedToken,
    StoredRefreshToken,
    Subscription,
    UserBalance,
)
from app.services import admin_user_service


@pytest.fixture()
def dbContext(monkeypatch) -> Iterator[dict]:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def getTestDb():
        with factory() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    monkeypatch.setattr(admin_user_service, "getDb", getTestDb)
    monkeypatch.setattr(admin_user_service, "recordAudit", lambda **_kwargs: None)
    yield {"factory": factory}
    engine.dispose()


def testPauseUserRevokesDevicesAndRefreshTokens(dbContext) -> None:
    factory = dbContext["factory"]
    now = datetime.now(UTC).replace(tzinfo=None)
    with factory() as db:
        user = IdentityUser(
            email="pause@example.com",
            passwordHash="!",
            displayName="Pause",
            tier="pro",
            status="active",
        )
        db.add(user)
        db.flush()
        device = IdentityDevice(
            userId=user.id,
            deviceId="device-pause",
            deviceName="Device",
            platform="test",
            status="active",
            firstSeenAt=now,
            lastSeenAt=now,
        )
        db.add(device)
        db.flush()
        token = StoredRefreshToken(
            jti="refresh-pause",
            tokenHash="hash-pause",
            userId=user.id,
            deviceId=device.id,
            expiresAt=now + timedelta(days=1),
        )
        db.add(token)
        db.commit()
        userId = user.id
        deviceId = device.id
        tokenId = token.id

    admin_user_service.updateUser(str(userId), status="paused")

    with factory() as db:
        pausedUser = db.get(IdentityUser, userId)
        assert pausedUser.status == "paused"
        assert pausedUser.authVersion == 1
        assert db.get(IdentityDevice, deviceId).status == "revoked"
        assert db.get(StoredRefreshToken, tokenId).revokedAt is not None


def testDeleteUserPermanentlyRemovesUserAndDependencies(dbContext) -> None:
    factory = dbContext["factory"]
    now = datetime.now(UTC).replace(tzinfo=None)
    expiresAt = now + timedelta(days=30)
    with factory() as db:
        user = IdentityUser(
            email="hard-delete@example.com",
            passwordHash="!",
            displayName="Hard Delete",
            tier="pro",
            status="active",
        )
        db.add(user)
        db.flush()
        userId = user.id
        device = IdentityDevice(
            userId=userId,
            deviceId="device-hard-delete",
            deviceName="Device",
            platform="test",
            status="active",
            firstSeenAt=now,
            lastSeenAt=now,
        )
        db.add_all(
            [
                UserBalance(userId=userId, balance=100, lifetimeGrant=100),
                device,
                PasswordResetToken(userId=userId, tokenHash="p" * 64, expiresAt=expiresAt),
                RevokedToken(
                    jti="revoked-hard-delete",
                    userId=userId,
                    tokenType="access",
                    reason="logout",
                    expiresAt=expiresAt,
                    revokedAt=now,
                ),
                IdempotencyKey(
                    userId=userId,
                    operation="test",
                    idempotencyKey="hard-delete-key",
                    requestHash="i" * 64,
                    expiresAt=expiresAt,
                ),
                RechargeRecord(
                    recordId="recharge-hard-delete",
                    userId=userId,
                    amount=100,
                    source="admin",
                    balanceBefore=0,
                    balanceAfter=100,
                ),
                Bill(
                    billId="bill-hard-delete",
                    userId=userId,
                    feature="test",
                    estimatedCost=10,
                    status="pending",
                    idempotencyKey="bill-hard-delete-key",
                    requestHash="b" * 64,
                    preauthExpiresAt=expiresAt,
                ),
                BalanceLedger(
                    userId=userId,
                    entryType="grant",
                    amount=100,
                    balanceDelta=100,
                    reservedDelta=0,
                    balanceAfter=100,
                    reservedAfter=0,
                    source="admin_grant",
                ),
            ]
        )
        db.flush()
        subscription = Subscription(
            userId=userId,
            planCode="pro",
            status="active",
            startedAt=now,
            currentPeriodStart=now,
            currentPeriodEnd=expiresAt,
            expiresAt=expiresAt,
            monthlyQuota=100,
        )
        licenseCode = LicenseCode(
            codeHash="c" * 64,
            codeKind="RCH",
            status="exhausted",
            amount=100,
            maxUses=1,
            usedCount=1,
            expiresAt=expiresAt,
        )
        db.add_all([subscription, licenseCode])
        db.flush()
        db.add(
            CodeRedemption(
                codeId=licenseCode.id,
                userId=userId,
                subscriptionId=subscription.id,
                amountGranted=100,
            )
        )
        db.flush()
        db.add(
            StoredRefreshToken(
                jti="refresh-hard-delete",
                tokenHash="r" * 64,
                userId=userId,
                deviceId=device.id,
                expiresAt=expiresAt,
            )
        )
        db.commit()

    admin_user_service.deleteUser(str(userId), confirm=str(userId))

    userModels = (
        BalanceLedger,
        Bill,
        CodeRedemption,
        IdempotencyKey,
        IdentityDevice,
        PasswordResetToken,
        RechargeRecord,
        RevokedToken,
        StoredRefreshToken,
        Subscription,
        UserBalance,
    )
    with factory() as db:
        assert db.get(IdentityUser, userId) is None
        for model in userModels:
            assert db.execute(select(model).where(model.userId == userId)).scalar_one_or_none() is None
        assert db.get(LicenseCode, licenseCode.id) is not None
