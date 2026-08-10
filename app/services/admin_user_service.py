"""Admin 用户管理服务。"""
from __future__ import annotations

import secrets
import string
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import func as saFunc
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.db import getDb
from app.errors import ApiError
from app.models import (
    BalanceLedger,
    RefreshToken,
    StoredRefreshToken,
    Subscription,
    UserAccount,
    UserBalance,
    UserDevice,
)
from app.security.password import hashPassword
from app.services.admin_audit_service import recordAudit
from app.services.identity_auth_service import normalizeEmail
from app.services.subscription_service import (
    createSubscription,
    getActiveSubscription,
)

VALID_TIERS = {"free", "pro", "team", "guest", "trial", "beta", "beta_pro", "paid"}
STATUS_ALIASES = {
    "active": "active",
    "paused": "paused",
    "banned": "banned",
    "deleted": "deleted",
    "suspended": "paused",
    "expired": "paused",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _parseCursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e


def _parseDateStart(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.combine(datetime.fromisoformat(value).date(), time.min)
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"日期格式错误: {e}") from e


def _parseDateEnd(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.combine(datetime.fromisoformat(value).date(), time.max)
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"日期格式错误: {e}") from e


def _parseUserId(userId: str | int) -> int:
    try:
        value = int(userId)
    except (TypeError, ValueError) as e:
        raise ApiError("BAD_REQUEST", "userId 必须为数字") from e
    if value <= 0:
        raise ApiError("BAD_REQUEST", "userId 必须为正整数")
    return value


def _normalizeStatus(status: str | None) -> str | None:
    if status is None or status == "":
        return None
    normalized = STATUS_ALIASES.get(status)
    if normalized is None:
        raise ApiError("BAD_REQUEST", f"status 必须为 {sorted(STATUS_ALIASES)} 之一")
    return normalized


def _ensureTier(tier: str | None) -> str | None:
    if tier is None or tier == "":
        return None
    if tier not in VALID_TIERS:
        raise ApiError("BAD_REQUEST", f"tier 必须为 {sorted(VALID_TIERS)} 之一")
    return tier


def _generatePassword() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(14))
        if any(c.isalpha() for c in password) and any(c.isdigit() for c in password):
            return password


def _balanceFor(userId: int) -> UserBalance:
    return UserBalance(userId=userId)


def _deviceStats(db, userId: int) -> tuple[int, datetime | None]:
    deviceCount = int(
        db.execute(
            select(saFunc.count()).select_from(UserDevice).where(UserDevice.userId == userId)
        ).scalar_one()
        or 0
    )
    lastSeenAt = db.execute(
        select(saFunc.max(UserDevice.lastSeenAt)).where(UserDevice.userId == userId)
    ).scalar_one_or_none()
    return deviceCount, lastSeenAt


def _toUserItem(db, user: UserAccount, balance: UserBalance | None) -> dict[str, Any]:
    balance = balance or _balanceFor(user.id)
    deviceCount, lastSeenAt = _deviceStats(db, user.id)
    return {
        "userId": str(user.id),
        "email": user.email,
        "displayName": user.displayName,
        "tier": user.tier,
        "status": user.status,
        "balance": int(balance.balance or 0),
        "totalSpent": int(balance.totalSpent or 0),
        "totalRecharged": int(balance.totalRecharged or 0),
        "activatedAt": user.createdAt,
        "registeredAt": user.createdAt,
        "lastSeenAt": lastSeenAt,
        "deviceCount": deviceCount,
        "deletedAt": user.deletedAt,
    }


def listUsers(
    limit: int = 50,
    cursor: str | None = None,
    q: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    registeredAfter: str | None = None,
    registeredBefore: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    limit = max(1, min(200, limit))
    normalizedStatus = _normalizeStatus(status)
    normalizedTier = _ensureTier(tier)
    after = _parseDateStart(registeredAfter)
    before = _parseDateEnd(registeredBefore)
    with getDb() as db:
        stmt = select(UserAccount, UserBalance).outerjoin(UserBalance, UserBalance.userId == UserAccount.id)
        cursorDt = _parseCursor(cursor)
        if cursorDt is not None:
            stmt = stmt.where(UserAccount.createdAt < cursorDt)
        if normalizedStatus:
            stmt = stmt.where(UserAccount.status == normalizedStatus)
        if normalizedTier:
            stmt = stmt.where(UserAccount.tier == normalizedTier)
        if after:
            stmt = stmt.where(UserAccount.createdAt >= after)
        if before:
            stmt = stmt.where(UserAccount.createdAt <= before)
        if q:
            like = f"%{q}%"
            filters = [UserAccount.displayName.like(like), UserAccount.email.like(like)]
            if q.isdigit():
                filters.append(UserAccount.id == int(q))
            stmt = stmt.where(or_(*filters))
        stmt = stmt.order_by(UserAccount.createdAt.desc()).limit(limit + 1)
        rows = db.execute(stmt).all()

        nextCursor: str | None = None
        if len(rows) > limit:
            lastRow = rows[limit - 1][0]
            nextCursor = lastRow.createdAt.isoformat()
            rows = rows[:limit]

        return [_toUserItem(db, user, balance) for user, balance in rows], nextCursor


def getUserDetail(userId: str) -> dict[str, Any]:
    numericUserId = _parseUserId(userId)
    with getDb() as db:
        user = db.get(UserAccount, numericUserId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        balance = db.get(UserBalance, numericUserId) or _balanceFor(numericUserId)
        item = _toUserItem(db, user, balance)
        item.update(
            {
                "frozenBalance": int(balance.frozenBalance or 0),
                "expireAt": None,
                "lifetimeGrant": int(balance.totalRecharged or 0),
                "lifetimeConsumed": int(balance.totalSpent or 0),
            }
        )
        return item


def createUser(
    email: str,
    password: str,
    displayName: str = "",
    tier: str = "free",
    status: str = "active",
) -> dict[str, Any]:
    normalizedTier = _ensureTier(tier) or "free"
    normalizedStatus = _normalizeStatus(status) or "active"
    normalizedEmail = normalizeEmail(email)
    try:
        passwordHash = hashPassword(password)
    except ValueError as e:
        raise ApiError("WEAK_PASSWORD", str(e), httpStatus=400) from e
    with getDb() as db:
        user = UserAccount(
            email=normalizedEmail,
            passwordHash=passwordHash,
            displayName=displayName.strip(),
            tier=normalizedTier,
            status=normalizedStatus,
            emailVerified=True,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as e:
            db.rollback()
            raise ApiError("EMAIL_ALREADY_USED", "邮箱已被使用", httpStatus=409) from e
        db.add(UserBalance(userId=user.id))
        db.commit()
        createdUserId = user.id

    recordAudit(
        actor="admin",
        action="admin.create_user",
        targetUser=str(createdUserId),
        details={"email": normalizedEmail, "tier": normalizedTier, "status": normalizedStatus},
    )
    return getUserDetail(str(createdUserId))


def updateUser(
    userId: str,
    tier: str | None = None,
    status: str | None = None,
    email: str | None = None,
    displayName: str | None = None,
) -> dict[str, Any]:
    normalizedTier = _ensureTier(tier)
    normalizedStatus = _normalizeStatus(status)
    normalizedEmail = normalizeEmail(email) if email else None
    numericUserId = _parseUserId(userId)
    with getDb() as db:
        user = db.get(UserAccount, numericUserId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        oldValues = {"tier": user.tier, "status": user.status, "email": user.email, "displayName": user.displayName}
        if normalizedTier:
            user.tier = normalizedTier
        if normalizedStatus:
            user.status = normalizedStatus
            if normalizedStatus != "deleted":
                user.deletedAt = None
        if normalizedEmail:
            user.email = normalizedEmail
        if displayName is not None:
            user.displayName = displayName.strip()
        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            raise ApiError("EMAIL_ALREADY_USED", "邮箱已被使用", httpStatus=409) from e
        newValues = {"tier": user.tier, "status": user.status, "email": user.email, "displayName": user.displayName}

    recordAudit(
        actor="admin",
        action="admin.update_user",
        targetUser=str(numericUserId),
        details={"old": oldValues, "new": newValues},
    )
    return {"userId": str(numericUserId), **newValues}


def updateUserTier(userId: str, tier: str, status: str | None = None) -> dict[str, Any]:
    return updateUser(userId=userId, tier=tier, status=status)


def grantBalance(userId: str, amount: int, note: str = "") -> dict[str, Any]:
    if amount <= 0:
        raise ApiError("BAD_REQUEST", "amount 必须 > 0")
    numericUserId = _parseUserId(userId)
    with getDb() as db:
        user = db.get(UserAccount, numericUserId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        balance = db.get(UserBalance, numericUserId)
        if balance is None:
            balance = UserBalance(userId=numericUserId)
            db.add(balance)
            db.flush()

        balance.balance += amount
        balance.totalRecharged += amount
        balance.version += 1
        afterBalance = balance.balance

        db.add(
            BalanceLedger(
                userId=numericUserId,
                entryType="grant",
                amount=amount,
                balanceDelta=amount,
                reservedDelta=0,
                balanceAfter=afterBalance,
                reservedAfter=int(balance.reserved or 0),
                source="admin_grant",
                refType="admin",
                note=note,
            )
        )
        db.commit()

    recordAudit(
        actor="admin",
        action="admin.grant_balance",
        targetUser=str(numericUserId),
        details={"amount": amount, "note": note},
    )
    return {"userId": str(numericUserId), "newBalance": afterBalance}


def revokeAllSessions(userId: str, reason: str = "") -> dict[str, Any]:
    numericUserId = _parseUserId(userId)
    now = _now()
    with getDb() as db:
        if db.get(UserAccount, numericUserId) is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        tokens = db.execute(
            select(RefreshToken).where(RefreshToken.userId == numericUserId, RefreshToken.revokedAt.is_(None))
        ).scalars().all()
        revoked = 0
        for token in tokens:
            token.revokedAt = now
            if hasattr(token, "revokeReason"):
                token.revokeReason = reason or "admin"
            revoked += 1
        db.commit()

    recordAudit(
        actor="admin",
        action="admin.revoke_sessions",
        targetUser=str(numericUserId),
        details={"revokedCount": revoked, "reason": reason},
    )
    return {"userId": str(numericUserId), "revokedCount": revoked}


def resetUserPassword(userId: str) -> dict[str, Any]:
    numericUserId = _parseUserId(userId)
    newPassword = _generatePassword()
    with getDb() as db:
        user = db.get(UserAccount, numericUserId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        user.passwordHash = hashPassword(newPassword)
        user.failedLoginCount = 0
        user.lockedUntil = None
        db.commit()

    revokeAllSessions(userId, "admin reset password")
    recordAudit(actor="admin", action="admin.reset_user_password", targetUser=str(numericUserId), details={})
    return {"userId": str(numericUserId), "newPassword": newPassword}


def deleteUser(userId: str, confirm: str = "") -> dict[str, Any]:
    numericUserId = _parseUserId(userId)
    if confirm != str(numericUserId):
        raise ApiError("BAD_REQUEST", "confirm 必须等于 userId")
    now = _now()
    with getDb() as db:
        user = db.get(UserAccount, numericUserId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        user.status = "deleted"
        user.deletedAt = now
        devices = db.execute(
            select(UserDevice).where(
                UserDevice.userId == numericUserId, UserDevice.status == "active"
            )
        ).scalars().all()
        for device in devices:
            device.status = "revoked"
            device.revokedAt = now
        tokens = db.execute(
            select(RefreshToken).where(
                RefreshToken.userId == numericUserId, RefreshToken.revokedAt.is_(None)
            )
        ).scalars().all()
        for token in tokens:
            token.revokedAt = now
            if hasattr(token, "revokeReason"):
                token.revokeReason = "admin_delete_user"
        db.commit()

    recordAudit(actor="admin", action="admin.delete_user", targetUser=str(numericUserId), details={"softDelete": True})
    return {"userId": str(numericUserId), "deletedAt": now}


def listUserSubscriptions(userId: str) -> list[dict[str, Any]]:
    numericUserId = _parseUserId(userId)
    with getDb() as db:
        if db.get(UserAccount, numericUserId) is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        items = db.execute(
            select(Subscription)
            .where(Subscription.userId == numericUserId)
            .order_by(Subscription.createdAt.desc())
            .limit(100)
        ).scalars().all()
        return [
            {
                "subscriptionId": str(item.id),
                "planCode": item.planCode,
                "status": item.status,
                "startedAt": item.startedAt,
                "currentPeriodStart": item.currentPeriodStart,
                "currentPeriodEnd": item.currentPeriodEnd,
                "monthlyQuota": int(item.monthlyQuota or 0),
                "autoRenew": bool(item.autoRenew),
            }
            for item in items
        ]


def createUserSubscription(userId: str, planCode: str) -> dict[str, Any]:
    """管理员为无有效订阅的用户开通一个预置订阅计划。"""
    numericUserId = _parseUserId(userId)
    with getDb() as db:
        user = db.execute(
            select(UserAccount)
            .where(UserAccount.id == numericUserId)
            .with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        if getActiveSubscription(db, numericUserId) is not None:
            raise ApiError(
                "CONFLICT",
                "该用户已有有效订阅，请等待到期后再开通新订阅",
                httpStatus=409,
            )
        subscription, grantResult = createSubscription(
            db,
            numericUserId,
            planCode.strip(),
            autoRenew=False,
        )
        payload = {
            "userId": str(numericUserId),
            "subscription": {
                "subscriptionId": str(subscription.id),
                "planCode": subscription.planCode,
                "status": subscription.status,
                "startedAt": subscription.startedAt,
                "currentPeriodStart": subscription.currentPeriodStart,
                "currentPeriodEnd": subscription.currentPeriodEnd,
                "monthlyQuota": int(subscription.monthlyQuota or 0),
                "autoRenew": bool(subscription.autoRenew),
            },
            "grantedBalance": grantResult.grantedBalance,
        }
        db.commit()

    recordAudit(
        actor="admin",
        action="admin.create_subscription",
        targetUser=str(numericUserId),
        details={
            "planCode": payload["subscription"]["planCode"],
            "subscriptionId": payload["subscription"]["subscriptionId"],
            "grantedBalance": payload["grantedBalance"],
        },
    )
    return payload


def listUserDevices(userId: str) -> list[dict[str, Any]]:
    numericUserId = _parseUserId(userId)
    with getDb() as db:
        if db.get(UserAccount, numericUserId) is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        items = db.execute(
            select(UserDevice)
            .where(UserDevice.userId == numericUserId)
            .order_by(UserDevice.lastSeenAt.desc())
            .limit(100)
        ).scalars().all()
        return [
            {
                "deviceId": item.deviceId,
                "deviceName": item.deviceName,
                "platform": item.platform,
                "status": item.status,
                "lastSeenAt": item.lastSeenAt,
                "createdAt": item.createdAt,
            }
            for item in items
        ]


def revokeUserDevice(userId: str, deviceId: str) -> dict[str, Any]:
    numericUserId = _parseUserId(userId)
    now = _now()
    with getDb() as db:
        device = db.execute(
            select(UserDevice)
            .where(UserDevice.userId == numericUserId, UserDevice.deviceId == deviceId)
            .with_for_update()
        ).scalar_one_or_none()
        if device is None:
            raise ApiError("NOT_FOUND", "设备不存在")
        device.status = "revoked"
        device.revokedAt = now
        tokens = db.execute(
            select(StoredRefreshToken)
            .where(
                StoredRefreshToken.deviceId == device.id, StoredRefreshToken.revokedAt.is_(None)
            )
        ).scalars().all()
        for token in tokens:
            token.revokedAt = now
            token.revokeReason = "admin_revoke_device"
        db.commit()

    recordAudit(
        actor="admin",
        action="admin.revoke_device",
        targetUser=str(numericUserId),
        details={"deviceId": deviceId},
    )
    return {"deviceId": deviceId, "status": "revoked"}


def listUserLedger(userId: str, limit: int = 20) -> list[dict[str, Any]]:
    numericUserId = _parseUserId(userId)
    limit = max(1, min(100, limit))
    with getDb() as db:
        if db.get(UserAccount, numericUserId) is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        items = db.execute(
            select(BalanceLedger)
            .where(BalanceLedger.userId == numericUserId)
            .order_by(BalanceLedger.createdAt.desc())
            .limit(limit)
        ).scalars().all()
        return [
            {
                "ledgerId": str(item.id),
                "type": item.entryType,
                "amount": int(item.balanceDelta or item.amount or 0),
                "source": item.source,
                "refId": item.refId,
                "note": item.note,
                "createdAt": item.createdAt,
            }
            for item in items
        ]


def batchUsers(action: str, userIds: list[str], status: str | None = None) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for rawUserId in userIds:
        try:
            if action == "update_status":
                results.append(updateUser(rawUserId, status=status))
            elif action == "reset_password":
                results.append(resetUserPassword(rawUserId))
            elif action == "delete":
                results.append(deleteUser(rawUserId, confirm=str(_parseUserId(rawUserId))))
            else:
                raise ApiError("BAD_REQUEST", "不支持的批量操作")
        except ApiError as e:
            results.append({"userId": rawUserId, "error": e.code, "message": e.message})
    successCount = sum(1 for item in results if "error" not in item)
    recordAudit(
        actor="admin",
        action="admin.batch_users",
        details={"action": action, "successCount": successCount, "total": len(userIds)},
    )
    failedCount = len(userIds) - successCount
    return {"action": action, "successCount": successCount, "failedCount": failedCount, "items": results}


__all__ = [
    "batchUsers",
    "createUser",
    "createUserSubscription",
    "deleteUser",
    "getUserDetail",
    "grantBalance",
    "listUserDevices",
    "listUserLedger",
    "listUserSubscriptions",
    "listUsers",
    "resetUserPassword",
    "revokeAllSessions",
    "revokeUserDevice",
    "updateUser",
    "updateUserTier",
]
