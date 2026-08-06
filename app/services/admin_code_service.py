"""Admin 凭证签发服务(2026-08-06 重构):

- issueCodes(kind, count, ...) → list[CodeItem]
    - 立即落库 license_codes(status='active')
    - 仅本次响应返回明文 code(rawCodeSignature + 明文)
    - signed payload 一次性 base64 编码(signed_code 兼容)
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import string
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy.orm import Session

from app.db import getDb
from app.errors import ApiError
from app.models import LicenseCode, UserTier
from app.security.hmac import hashCode, signPayload
from app.services.admin_audit_service import recordAudit


def _genCodeBody(prefix: str) -> str:
    """形如 INV-AB12-CD34-EF56-GH78 的码体(排除 0/O/1/I/L)。"""
    alphabet = "".join(
        c for c in (string.ascii_uppercase + string.digits) if c not in "0O1IL"
    )
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return f"{prefix}-" + "-".join(parts)


def _buildSignedPayload(
    codeBody: str,
    kind: str,
    grantedBalance: int,
    grantedDays: int,
    tier: str,
    amount: int,
    expireAt: datetime,
) -> str:
    """构造 signed payload 并返回 base64(json+sig)。

    与客户端 signed_code.py 兼容:`base64(JSON(payload + signature))`。
    """
    base: dict[str, Any] = {
        "code": codeBody,
        "expireAt": expireAt.isoformat(),
        "issuedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "version": 1,
    }
    if kind == "invite":
        base.update(
            {
                "maxUses": 1,
                "grantedBalance": grantedBalance,
                "grantedDays": grantedDays,
                "tier": tier,
            }
        )
    elif kind == "trial":
        base.update(
            {
                "maxUses": 1,
                "grantedBalance": grantedBalance,
                "grantedDays": grantedDays,
                "tier": UserTier.TRIAL.value,
            }
        )
    elif kind == "recharge":
        base.update({"amount": amount, "note": "admin issued"})
    base["signature"] = signPayload(base)
    return base64.b64encode(
        json.dumps(base, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


def issueCodes(
    kind: str,
    count: int,
    grantedBalance: int = 100,
    grantedDays: int = 30,
    tier: str = "beta",
    amount: int = 0,
    expireDays: int = 14,
    issuedBy: str = "admin",
) -> list[dict[str, Any]]:
    """批量签发凭证(同时持久化到 license_codes 表)。"""
    if kind not in ("invite", "trial", "recharge"):
        raise ApiError("BAD_REQUEST", "kind 必须为 invite/trial/recharge")
    if count < 1 or count > 1000:
        raise ApiError("BAD_REQUEST", "count 必须在 1~1000 之间")
    if kind == "recharge" and amount <= 0:
        raise ApiError("BAD_REQUEST", "充值码必须指定 amount > 0")

    prefixMap = {"invite": "INV", "trial": "TRY", "recharge": "RCH"}
    expireAt = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=expireDays)

    items: list[dict[str, Any]] = []
    with getDb() as db:
        for _ in range(count):
            codeBody = _genCodeBody(prefixMap[kind])
            codeHash = hashCode(codeBody)
            signedRaw = _buildSignedPayload(
                codeBody, kind, grantedBalance, grantedDays, tier, amount, expireAt
            )
            row = LicenseCode(
                codeHash=codeHash,
                codeKind=kind,
                status="active",
                grantedBalance=grantedBalance if kind in ("invite", "trial") else None,
                grantedDays=grantedDays if kind in ("invite", "trial") else None,
                tier=tier if kind in ("invite", "trial") else None,
                amount=amount if kind == "recharge" else None,
                issuedBy=issuedBy,
                expireAt=expireAt,
                rawCodeSignature=signedRaw,
            )
            db.add(row)
            db.flush()  # 留 audit_id 之类的可读字段(本表无,但保 flush 统一)

            items.append(
                {
                    "codeHash": codeHash,
                    "code": codeBody,  # 明文,仅本次签发返回
                    "signedPayload": signedRaw,  # base64(json+sig),供前端用
                    "codeKind": kind,
                    "status": "active",
                    "grantedBalance": grantedBalance if kind in ("invite", "trial") else None,
                    "grantedDays": grantedDays if kind in ("invite", "trial") else None,
                    "tier": tier if kind in ("invite", "trial") else None,
                    "amount": amount if kind == "recharge" else None,
                    "issuedBy": issuedBy,
                    "issuedAt": row.issuedAt,
                    "expireAt": expireAt,
                }
            )
        db.commit()

    recordAudit(
        actor=issuedBy,
        action="admin.issue_codes",
        details={
            "kind": kind,
            "count": count,
            "grantedBalance": grantedBalance,
            "grantedDays": grantedDays,
            "amount": amount,
        },
    )
    logger.info(f"[AdminCode] issue kind={kind} count={count}")
    return items


__all__ = ["issueCodes"]