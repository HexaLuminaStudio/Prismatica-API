"""Admin CSV 导出服务(2026-08-06 新增)。

每个函数返回"行字典列表",由路由层序列化为 CSV。
所有时间字段输出 ISO 字符串;None 输出空串,便于 Excel 直接打开。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.db import getDb
from app.models import AuditLog, Bill, LicenseCode, UserAccount, UserBalance


def _iso(dt: Any) -> str:
    """datetime → ISO 字符串(空值输出空串)。"""
    return dt.isoformat() if dt else ""


def exportUsers(limit: int = 5000) -> list[dict[str, Any]]:
    """用户列表全量导出(含余额三件套)。"""
    limit = max(1, min(10000, limit))
    with getDb() as db:
        stmt = (
            select(UserAccount, UserBalance)
            .outerjoin(UserBalance, UserBalance.userId == UserAccount.id)
            .order_by(UserAccount.createdAt.desc())
            .limit(limit)
        )
        rows = db.execute(stmt).all()
        return [
            {
                "userId": str(a.id),
                "displayName": a.displayName,
                "tier": a.tier,
                "status": a.status,
                "balance": int(b.balance or 0) if b else 0,
                "totalRecharged": int(b.totalRecharged or 0) if b else 0,
                "totalSpent": int(b.totalSpent or 0) if b else 0,
                "activatedAt": _iso(a.createdAt),
                "expireAt": "",
                "createdAt": _iso(a.createdAt),
            }
            for a, b in rows
        ]


def exportAudit(
    limit: int = 5000,
    days: int | None = None,
    action: str | None = None,
    actor: str | None = None,
    targetUser: str | None = None,
) -> list[dict[str, Any]]:
    """审计日志导出(过滤同 listAudit)。"""
    limit = max(1, min(10000, limit))
    with getDb() as db:
        stmt = select(AuditLog).order_by(AuditLog.createdAt.desc()).limit(limit)
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if actor:
            stmt = stmt.where(AuditLog.actor == actor)
        if targetUser:
            stmt = stmt.where(AuditLog.targetUser == targetUser)
        if days:
            since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=int(days))
            stmt = stmt.where(AuditLog.createdAt >= since)
        rows = db.execute(stmt).scalars().all()
        return [
            {
                "auditId": r.auditId,
                "createdAt": _iso(r.createdAt),
                "actor": r.actor,
                "action": r.action,
                "targetUser": r.targetUser or "",
                "ip": r.ip or "",
                "details": json.dumps(r.details or {}, ensure_ascii=False),
            }
            for r in rows
        ]


def exportCodes(
    limit: int = 5000,
    kind: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """凭证列表导出(不含明文 code,仅 hash)。"""
    limit = max(1, min(10000, limit))
    with getDb() as db:
        stmt = select(LicenseCode).order_by(LicenseCode.issuedAt.desc()).limit(limit)
        if kind:
            kindMap = {"invite": "INV", "trial": "TRY", "recharge": "RCH"}
            stmt = stmt.where(LicenseCode.codeKind == kindMap.get(kind, kind))
        if status:
            stmt = stmt.where(LicenseCode.status == status)
        rows = db.execute(stmt).scalars().all()
        return [
            {
                "codeHash": r.codeHash,
                "codeKind": {"INV": "invite", "TRY": "trial", "RCH": "recharge"}.get(
                    r.codeKind, r.codeKind
                ),
                "status": r.status,
                "grantedBalance": r.monthlyQuota if r.monthlyQuota is not None else "",
                "grantedDays": (
                    r.trialDays
                    if r.codeKind == "TRY"
                    else (r.periodMonths * 30 if r.periodMonths is not None else "")
                ),
                "tier": r.planCode or ("pro" if r.codeKind == "TRY" else ""),
                "amount": r.amount if r.amount is not None else "",
                "issuedBy": r.issuedBy,
                "issuedAt": _iso(r.issuedAt),
                "expireAt": _iso(r.expiresAt),
                "consumedAt": "",
                "consumedByUserId": "",
                "consumedIp": "",
            }
            for r in rows
        ]


def exportBills(
    limit: int = 5000,
    status: str | None = None,
    userId: str | None = None,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """账单流水导出(含用户 displayName)。"""
    limit = max(1, min(10000, limit))
    with getDb() as db:
        stmt = (
            select(Bill, UserAccount.displayName)
            .outerjoin(UserAccount, UserAccount.id == Bill.userId)
            .order_by(Bill.createdAt.desc())
            .limit(limit)
        )
        if status:
            stmt = stmt.where(Bill.status == status)
        if userId:
            try:
                numericUserId = int(userId)
            except ValueError as error:
                from app.errors import ApiError

                raise ApiError("BAD_REQUEST", "userId 必须为数字") from error
            stmt = stmt.where(Bill.userId == numericUserId)
        if days:
            since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=int(days))
            stmt = stmt.where(Bill.createdAt >= since)
        rows = db.execute(stmt).all()
        return [
            {
                "billId": r.billId,
                "userId": str(r.userId),
                "displayName": displayName or "",
                "actionType": r.feature,
                "actionDisplayName": r.feature,
                "estimatedCost": int(r.estimatedCost or 0),
                "realCost": int(r.actualCost or 0),
                "resourceUsed": 0,
                "balanceBefore": 0,
                "balanceAfter": 0,
                "status": r.status,
                "taskId": "",
                "description": r.description,
                "idempotencyKey": r.idempotencyKey or "",
                "createdAt": _iso(r.createdAt),
                "settledAt": _iso(r.settledAt),
            }
            for r, displayName in rows
        ]


__all__ = ["exportUsers", "exportAudit", "exportCodes", "exportBills"]
