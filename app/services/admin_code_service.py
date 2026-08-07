"""Admin 兑换码签发服务。"""
from __future__ import annotations

import base64
import json
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger

from app.db import getDb
from app.errors import ApiError
from app.models import LicenseCode
from app.security.hmac import hashCode, signPayload
from app.services.admin_audit_service import recordAudit

KIND_MAP = {"invite": "INV", "trial": "TRY", "recharge": "RCH", "INV": "INV", "TRY": "TRY", "RCH": "RCH"}
DISPLAY_KIND_MAP = {"INV": "invite", "TRY": "trial", "RCH": "recharge"}


def _normalizeKind(kind: str) -> str:
    """转换兑换码类型。"""
    normalized = KIND_MAP.get(kind)
    if normalized is None:
        raise ApiError("BAD_REQUEST", "kind 必须为 invite/trial/recharge 或 INV/TRY/RCH")
    return normalized


def _genCodeBody(prefix: str) -> str:
    """生成兑换码明文。"""
    alphabet = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "0O1IL")
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return f"{prefix}-" + "-".join(parts)


def _buildSignedPayload(
    codeBody: str,
    kind: str,
    grantedBalance: int,
    grantedDays: int,
    tier: str,
    amount: int,
    expiresAt: datetime,
) -> str:
    """构造 signed payload。"""
    payload: dict[str, Any] = {
        "code": codeBody,
        "expireAt": expiresAt.isoformat(),
        "issuedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "version": 1,
    }
    if kind == "INV":
        payload.update({"maxUses": 1, "grantedBalance": grantedBalance, "grantedDays": grantedDays, "tier": tier})
    elif kind == "TRY":
        payload.update({"maxUses": 1, "grantedBalance": grantedBalance, "grantedDays": grantedDays, "tier": "pro"})
    elif kind == "RCH":
        payload.update({"amount": amount, "note": "admin issued"})
    payload["signature"] = signPayload(payload)
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def issueCodes(
    kind: str,
    count: int,
    grantedBalance: int = 100,
    grantedDays: int = 30,
    tier: str = "pro",
    amount: int = 0,
    expireDays: int = 14,
    issuedBy: str = "admin",
) -> list[dict[str, Any]]:
    """批量签发兑换码。"""
    codeKind = _normalizeKind(kind)
    if count < 1 or count > 1000:
        raise ApiError("BAD_REQUEST", "count 必须在 1~1000 之间")
    if codeKind == "RCH" and amount <= 0:
        raise ApiError("BAD_REQUEST", "充值码必须指定 amount > 0")
    if tier not in {"free", "pro", "team"}:
        raise ApiError("BAD_REQUEST", "tier 必须为 free/pro/team 之一")

    expiresAt = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=expireDays)
    items: list[dict[str, Any]] = []
    with getDb() as db:
        for _ in range(count):
            codeBody = _genCodeBody(codeKind)
            codeHash = hashCode(codeBody)
            signedRaw = _buildSignedPayload(codeBody, codeKind, grantedBalance, grantedDays, tier, amount, expiresAt)
            row = LicenseCode(
                codeHash=codeHash,
                codeKind=codeKind,
                status="active",
                planCode=tier if codeKind == "INV" else None,
                periodMonths=max(1, grantedDays // 30) if codeKind == "INV" else None,
                trialDays=grantedDays if codeKind == "TRY" else None,
                monthlyQuota=grantedBalance if codeKind in {"INV", "TRY"} else None,
                amount=amount if codeKind == "RCH" else None,
                maxUses=1,
                usedCount=0,
                issuedBy=None,
                note="admin issued",
                expiresAt=expiresAt,
            )
            db.add(row)
            db.flush()
            displayKind = DISPLAY_KIND_MAP[codeKind]
            items.append(
                {
                    "codeHash": codeHash,
                    "code": codeBody,
                    "signedPayload": signedRaw,
                    "codeKind": displayKind,
                    "status": "active",
                    "grantedBalance": grantedBalance if codeKind in {"INV", "TRY"} else None,
                    "grantedDays": grantedDays if codeKind in {"INV", "TRY"} else None,
                    "tier": tier if codeKind == "INV" else ("pro" if codeKind == "TRY" else None),
                    "amount": amount if codeKind == "RCH" else None,
                    "issuedBy": issuedBy,
                    "issuedAt": row.issuedAt,
                    "expireAt": expiresAt,
                }
            )
        db.commit()

    recordAudit(
        actor=issuedBy,
        action="admin.issue_codes",
        details={
            "kind": codeKind,
            "count": count,
            "grantedBalance": grantedBalance,
            "grantedDays": grantedDays,
            "amount": amount,
        }
    )
    logger.info(f"[AdminCode] issue kind={codeKind} count={count}")
    return items


__all__ = ["issueCodes"]
