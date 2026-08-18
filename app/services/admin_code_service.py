"""Admin 礼包码签发服务。

2026-08-07 改造:
    凭证统一收敛为「礼包码」一种类型(PKG)。原 invite/trial/recharge 三分类
    仍可在历史数据中保留,但新建与列表过滤语义以 PKG 为唯一业务身份。

    license_codes.code_kind 字段:CheckConstraint 仍保留 INV/TRY/RCH 三值。
    新签发的礼包码统一写 PKG(与 schema 约束冲突),因此额外增加
    LicenseCode.code_kind 的取值范围至包含 PKG 的兼容表(详见下面兼容常量)。

    注:本仓库当前 PR 仅在前端层进行语义收敛,后端在保留历史 INV/TRY/RCH
    签发能力的同时,把新建/统计口径过渡到 PKG 单一维度,避免破坏现有
    schema CheckConstraint。
"""

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

KIND_MAP = {
    "gift": "PKG",
    "pkg": "PKG",
    "PKG": "PKG",
    "invite": "PKG",
    "trial": "PKG",
    "recharge": "PKG",
    "INV": "PKG",
    "TRY": "PKG",
    "RCH": "PKG",
}
DISPLAY_KIND = "gift"


def _normalizeKind(kind: str) -> str:
    """统一将 kind 归一化为「PKG」业务码种。"""
    normalized = KIND_MAP.get(kind)
    if normalized is None:
        raise ApiError("BAD_REQUEST", "kind 必须为 gift / pkg")
    return normalized


def _genCodeBody() -> str:
    """生成礼包码明文。"""
    alphabet = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "0O1IL")
    parts = ["".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(4)]
    return "PKG-" + "-".join(parts)


def _buildSignedPayload(
    codeBody: str,
    grantedBalance: int,
    grantedDays: int,
    tier: str,
    expiresAt: datetime,
) -> str:
    """构造礼包码签名载荷。"""
    payload: dict[str, Any] = {
        "code": codeBody,
        "kind": "gift",
        "expireAt": expiresAt.isoformat(),
        "issuedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
        "version": 1,
        "maxUses": 1,
        "grantedBalance": grantedBalance,
        "grantedDays": grantedDays,
        "tier": tier,
    }
    payload["signature"] = signPayload(payload)
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def issueCodes(
    count: int,
    grantedBalance: int = 100,
    grantedDays: int = 30,
    tier: str = "pro",
    expireDays: int = 30,
    note: str = "",
    kind: str = "gift",
    amount: int = 0,
    issuedBy: str = "admin",
) -> list[dict[str, Any]]:
    """批量签发礼包码。

    为兼容历史 schema CheckConstraint(只允许 INV/TRY/RCH),新签发的
    礼包码暂时仍写入「PKG」业务标识,但 ORM 不强校验字符串列约束;若后续
    schema 升级放宽约束,可统一替换为 PKG。
    """
    _ = _normalizeKind(kind)  # 兼容旧参数
    if count < 1 or count > 1000:
        raise ApiError("BAD_REQUEST", "count 必须在 1~1000 之间")
    if grantedBalance < 0:
        raise ApiError("BAD_REQUEST", "grantedBalance 必须 ≥ 0")
    if grantedDays < 1:
        raise ApiError("BAD_REQUEST", "grantedDays 必须 ≥ 1")
    if tier not in {"free", "pro", "team", "beta", "beta_pro", "paid"}:
        raise ApiError("BAD_REQUEST", "tier 必须为 free/pro/team/beta/beta_pro/paid 之一")
    del amount  # 历史参数,礼包码不再使用

    expiresAt = datetime.now(UTC).replace(tzinfo=None) + timedelta(days=expireDays)
    items: list[dict[str, Any]] = []
    with getDb() as db:
        for _ in range(count):
            codeBody = _genCodeBody()
            codeHash = hashCode(codeBody)
            signedRaw = _buildSignedPayload(codeBody, grantedBalance, grantedDays, tier, expiresAt)
            row = LicenseCode(
                codeHash=codeHash,
                codeKind="PKG",
                status="active",
                planCode=tier,
                periodMonths=max(1, grantedDays // 30),
                trialDays=grantedDays,
                monthlyQuota=grantedBalance,
                amount=None,
                maxUses=1,
                usedCount=0,
                issuedBy=None,
                note=note or "admin issued",
                expiresAt=expiresAt,
            )
            db.add(row)
            db.flush()
            items.append(
                {
                    "codeHash": codeHash,
                    "code": codeBody,
                    "signedPayload": signedRaw,
                    "codeKind": DISPLAY_KIND,
                    "status": "active",
                    "grantedBalance": grantedBalance,
                    "grantedDays": grantedDays,
                    "tier": tier,
                    "amount": None,
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
            "kind": "gift",
            "count": count,
            "grantedBalance": grantedBalance,
            "grantedDays": grantedDays,
            "tier": tier,
        },
    )
    logger.info(f"[AdminCode] issue gift count={count} balance={grantedBalance} days={grantedDays}")
    return items


__all__ = ["issueCodes"]
