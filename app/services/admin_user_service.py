"""Admin 用户管理服务。"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func as saFunc
from sqlalchemy import select

from app.db import getDb
from app.errors import ApiError
from app.models import RechargeRecord, RefreshToken, UserAccount, UserBalance, UserDevice
from app.services.admin_audit_service import recordAudit


def _parseCursor(cursor: str | None) -> datetime | None:
    """解析 cursor。"""
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e


def _parseUserId(userId: str | int) -> int:
    """解析用户主键。"""
    try:
        value = int(userId)
    except (TypeError, ValueError) as e:
        raise ApiError("BAD_REQUEST", "userId 必须为数字") from e
    if value <= 0:
        raise ApiError("BAD_REQUEST", "userId 必须为正整数")
    return value


def listUsers(
    limit: int = 50,
    cursor: str | None = None,
    q: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """用户列表。"""
    limit = max(1, min(200, limit))
    with getDb() as db:
        stmt = select(UserAccount, UserBalance).outerjoin(
            UserBalance, UserBalance.userId == UserAccount.id
        )
        cursorDt = _parseCursor(cursor)
        if cursorDt is not None:
            stmt = stmt.where(UserAccount.createdAt < cursorDt)
        if q:
            like = f"%{q}%"
            filters = [UserAccount.displayName.like(like), UserAccount.email.like(like)]
            if q.isdigit():
                filters.append(UserAccount.id == int(q))
            stmt = stmt.where(filters[0] | filters[1] | filters[2] if len(filters) == 3 else filters[0] | filters[1])
        stmt = stmt.order_by(UserAccount.createdAt.desc()).limit(limit + 1)
        rows = db.execute(stmt).all()

        nextCursor: str | None = None
        if len(rows) > limit:
            lastRow = rows[limit - 1][0]
            nextCursor = lastRow.createdAt.isoformat()
            rows = rows[:limit]

        items: list[dict[str, Any]] = []
        for acct, bal in rows:
            bal = bal or UserBalance(userId=acct.id)
            items.append(
                {
                    "userId": str(acct.id),
                    "displayName": acct.displayName,
                    "tier": acct.tier,
                    "status": acct.status,
                    "balance": int(bal.balance or 0),
                    "totalSpent": int(bal.totalSpent or 0),
                    "totalRecharged": int(bal.totalRecharged or 0),
                    "activatedAt": acct.createdAt,
                }
            )
        return items, nextCursor


def getUserDetail(userId: str) -> dict[str, Any]:
    """用户详情。"""
    numericUserId = _parseUserId(userId)
    with getDb() as db:
        acct = db.get(UserAccount, numericUserId)
        if acct is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        balance = db.get(UserBalance, numericUserId) or UserBalance(userId=numericUserId)

        deviceCount = int(
            db.execute(
                select(saFunc.count()).select_from(UserDevice).where(
                    UserDevice.userId == numericUserId
                )
            ).scalar_one()
            or 0
        )
        lastSeen = db.execute(
            select(saFunc.max(UserDevice.lastSeenAt)).where(UserDevice.userId == numericUserId)
        ).scalar_one_or_none()

        return {
            "userId": str(acct.id),
            "displayName": acct.displayName,
            "tier": acct.tier,
            "status": acct.status,
            "balance": int(balance.balance or 0),
            "frozenBalance": int(balance.frozenBalance or 0),
            "totalSpent": int(balance.totalSpent or 0),
            "totalRecharged": int(balance.totalRecharged or 0),
            "activatedAt": acct.createdAt,
            "expireAt": None,
            "lastSeenAt": lastSeen,
            "deviceCount": deviceCount,
        }


def updateUserTier(userId: str, tier: str, status: str | None = None) -> dict[str, Any]:
    """更新用户 tier / status。"""
    validTiers = {"free", "pro", "team"}
    if tier not in validTiers:
        raise ApiError("BAD_REQUEST", f"tier 必须为 {sorted(validTiers)} 之一")
    validStatus = {"active", "suspended", "expired"}
    if status and status not in validStatus:
        raise ApiError("BAD_REQUEST", f"status 必须为 {sorted(validStatus)} 之一")

    numericUserId = _parseUserId(userId)
    with getDb() as db:
        user = db.get(UserAccount, numericUserId)
        if user is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        oldTier = user.tier
        user.tier = tier
        if status:
            user.status = status
        finalStatus = user.status
        db.commit()

    recordAudit(
        actor="admin",
        action="admin.update_tier",
        targetUser=str(numericUserId),
        details={"oldTier": oldTier, "newTier": tier, "status": finalStatus},
    )
    return {"userId": str(numericUserId), "tier": tier, "status": finalStatus}


def grantBalance(userId: str, amount: int, note: str = "") -> dict[str, Any]:
    """手动加余额。"""
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

        beforeBalance = balance.balance
        balance.balance += amount
        balance.totalRecharged += amount
        balance.version += 1
        afterBalance = balance.balance

        db.add(
            RechargeRecord(
                recordId=str(uuid.uuid4()),
                userId=numericUserId,
                amount=amount,
                source="admin_grant",
                operatorNote=note,
                balanceBefore=beforeBalance,
                balanceAfter=afterBalance,
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
    """撤销某用户的所有 refresh_token。"""
    numericUserId = _parseUserId(userId)
    now = datetime.now(UTC).replace(tzinfo=None)
    with getDb() as db:
        if db.get(UserAccount, numericUserId) is None:
            raise ApiError("NOT_FOUND", "用户不存在")
        result = db.execute(
            select(RefreshToken).where(
                RefreshToken.userId == numericUserId,
                RefreshToken.revokedAt.is_(None),
            )
        ).scalars().all()
        revoked = 0
        for tok in result:
            tok.revokedAt = now
            revoked += 1
        db.commit()

    recordAudit(
        actor="admin",
        action="admin.revoke_sessions",
        targetUser=str(numericUserId),
        details={"revokedCount": revoked, "reason": reason},
    )
    return {"userId": str(numericUserId), "revokedCount": revoked}


__all__ = [
    "listUsers",
    "getUserDetail",
    "updateUserTier",
    "grantBalance",
    "revokeAllSessions",
]
