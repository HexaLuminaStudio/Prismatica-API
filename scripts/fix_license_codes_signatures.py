"""license_codes.raw_code_signature 修复脚本(2026-08-06)

背景:
    PrismaticaAPI redeem 流程:
        用户输入明文 INV-/TRY-/RCH- 码 → 后端 sha256 查 license_codes
        → 拿 raw_code_signature 反向 base64+JSON 解码 → 验签 → 兑换

    如果 raw_code_signature 损坏(老格式 / 字符截断 / 编码不一致),
    反查就抛 "Incorrect padding",redeem 失败。

脚本能力:
    1. dry-run(--dry-run,默认):扫所有 license_codes,对每一行尝试 base64+JSON 解析,
       报告损坏行(codeHash / codeKind / status / 错误摘要),不动数据。
    2. repair(--repair <mapping.json>):从 JSON 文件读 {codeHash: codeBody, ...} 映射,
       对每一行重建 signed payload 并 UPDATE raw_code_signature。

    JSON 文件格式(任一形式):
        - {"<codeHash>": "<codeBody>", ...}
        - [{"codeHash": "<codeHash>", "codeBody": "<codeBody>", "issuedAt": "...", "expireAt": "..."}, ...]
            (后一种形式允许修复时显式指定 issuedAt/expireAt,但 issuedAt 默认沿用原值,expireAt 同上)

用法:
    # 仅诊断,不写库
    python -m scripts.fix_license_codes_signatures --dry-run

    # 修复:把 mapping.json 里的 codeHash → codeBody 重新签发
    python -m scripts.fix_license_codes_signatures --repair mapping.json

    # 过滤只诊断某种 kind
    python -m scripts.fix_license_codes_signatures --dry-run --kind invite

依赖:与 admin_code_service._buildSignedPayload 严格对齐,
     保证重签后的 base64 signed payload 与历史合法格式一致。
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


from loguru import logger  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import getSettings  # noqa: E402
from app.db import engine  # noqa: E402
from app.models.user import UserTier  # noqa: E402
from app.security.hmac import hashCode, signPayload  # noqa: E402

# ---------------------------------------------------------------------------
# 诊断:检查一行 raw_code_signature 能否被 base64+JSON 解析
# ---------------------------------------------------------------------------


def _tryParseSigned(signed: str | None) -> tuple[bool, str]:
    """返回 (ok, info)。ok=False 时 info 是错误摘要。"""
    if not signed:
        return False, "raw_code_signature is NULL/empty"
    try:
        decoded = base64.b64decode(signed).decode("utf-8")
        data = json.loads(decoded)
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"
    if not isinstance(data, dict):
        return False, "decoded payload is not a dict"
    if "signature" not in data:
        return False, "decoded payload missing 'signature' field"
    if "code" not in data:
        return False, "decoded payload missing 'code' field"
    return True, "ok"


# ---------------------------------------------------------------------------
# 重建 signed payload(严格复刻 admin_code_service._buildSignedPayload)
# ---------------------------------------------------------------------------


def _rebuildSignedPayload(
    *,
    codeBody: str,
    kind: str,
    grantedBalance: int | None,
    grantedDays: int | None,
    tier: str | None,
    amount: int | None,
    expireAt: datetime,
) -> str:
    """重建 base64(json+sig) signed payload。

    与 admin_code_service._buildSignedPayload 字段顺序、JSON 默认设置完全一致:
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        + base64.b64encode
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
                "grantedBalance": int(grantedBalance or 0),
                "grantedDays": int(grantedDays or 0),
                "tier": tier or "beta",
            }
        )
    elif kind == "trial":
        base.update(
            {
                "maxUses": 1,
                "grantedBalance": int(grantedBalance or 0),
                "grantedDays": int(grantedDays or 0),
                "tier": UserTier.TRIAL.value,
            }
        )
    elif kind == "recharge":
        base.update({"amount": int(amount or 0), "note": "admin issued"})
    base["signature"] = signPayload(base)
    return base64.b64encode(
        json.dumps(base, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")


# ---------------------------------------------------------------------------
# dry-run:扫描所有行,统计损坏情况
# ---------------------------------------------------------------------------


def runDryRun(*, kind: str | None) -> int:
    settings = getSettings()
    logger.info(f"[fix] dry-run db={settings.dbHost}/{settings.dbName}")
    total = 0
    bad = 0
    samples: list[dict[str, Any]] = []
    with engine.begin() as conn:
        # 直接走 core SQL,避免 ORM 在 low-level connection 上偶尔把
        # 实体当成 tuple 透传回来的坑;只拉本脚本关心的几列。
        sql = (
            "SELECT code_hash, code_kind, status, issued_by, issued_at, expire_at, "
            "raw_code_signature FROM license_codes"
        )
        params: dict[str, Any] = {}
        if kind:
            sql += " WHERE code_kind = :kind"
            params["kind"] = kind
        rows = conn.execute(text(sql), params).all()
        for row in rows:
            total += 1
            codeHash, codeKind, status, issuedBy, issuedAt, expireAt, signed = row
            ok, info = _tryParseSigned(signed)
            if not ok:
                bad += 1
                if len(samples) < 20:
                    samples.append(
                        {
                            "codeHash": codeHash,
                            "codeKind": codeKind,
                            "status": status,
                            "issuedBy": issuedBy,
                            "issuedAt": issuedAt.isoformat() if issuedAt else None,
                            "expireAt": expireAt.isoformat() if expireAt else None,
                            "error": info,
                            "signedLen": len(signed or ""),
                        }
                    )

    logger.info(
        f"[fix] scanned={total} bad={bad} ok={total - bad} "
        f"({(bad / total * 100) if total else 0:.1f}% bad)"
    )
    if samples:
        logger.warning("[fix] 损坏样例(最多 20 条):")
        for s in samples:
            logger.warning(
                f"  - codeHash={s['codeHash'][:12]}… kind={s['codeKind']} "
                f"status={s['status']} signedLen={s['signedLen']} err={s['error']}"
            )
    return 0


# ---------------------------------------------------------------------------
# repair:从 mapping 文件读 {codeHash: codeBody},重建并 UPDATE
# ---------------------------------------------------------------------------


def _loadMapping(path: Path) -> dict[str, str]:
    """支持两种 JSON 形态。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return {str(k).strip(): str(v).strip() for k, v in raw.items()}
    if isinstance(raw, list):
        out: dict[str, str] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            ch = item.get("codeHash")
            cb = item.get("codeBody") or item.get("code")
            if ch and cb:
                out[str(ch).strip()] = str(cb).strip()
        return out
    raise ValueError("mapping 文件格式非法: 顶层必须是 dict 或 list")


def runRepair(*, mappingPath: Path) -> int:
    settings = getSettings()
    logger.info(f"[fix] repair db={settings.dbHost}/{settings.dbName} src={mappingPath}")
    mapping = _loadMapping(mappingPath)
    if not mapping:
        logger.warning("[fix] mapping 为空,无操作")
        return 0

    updated = 0
    skipped = 0
    missing = 0
    failed = 0
    with engine.begin() as conn:
        for codeHash, codeBody in mapping.items():
            # 二次确认 sha256(codeBody) == codeHash(避免人肉 mapping 写错)
            if hashCode(codeBody) != codeHash:
                logger.error(
                    f"[fix] sha256(codeBody) != codeHash,"
                    f"codeHash={codeHash[:12]}… 跳过"
                )
                failed += 1
                continue

            row = conn.execute(
                text(
                    "SELECT code_kind, granted_balance, granted_days, tier, amount, expire_at "
                    "FROM license_codes WHERE code_hash = :ch"
                ),
                {"ch": codeHash},
            ).first()
            if row is None:
                logger.error(
                    f"[fix] license_codes 表中无该 codeHash={codeHash[:12]}…"
                )
                missing += 1
                continue

            codeKind, grantedBalance, grantedDays, tier, amount, expireAt = row
            # 如果 status 已 consumed/revoked/expired,虽然 signed 损坏但也没人兑了,
            # 但仍建议修(留作历史数据完整性);不做强制跳过
            newSigned = _rebuildSignedPayload(
                codeBody=codeBody,
                kind=codeKind,
                grantedBalance=grantedBalance,
                grantedDays=grantedDays,
                tier=tier,
                amount=amount,
                expireAt=expireAt or datetime.now(UTC).replace(tzinfo=None),
            )
            ok, info = _tryParseSigned(newSigned)
            if not ok:
                logger.error(
                    f"[fix] 重建后仍不可解析?! codeHash={codeHash[:12]}… info={info}"
                )
                failed += 1
                continue

            conn.execute(
                text(
                    "UPDATE license_codes SET raw_code_signature = :s "
                    "WHERE code_hash = :ch"
                ),
                {"s": newSigned, "ch": codeHash},
            )
            updated += 1
            logger.info(
                f"[fix] updated codeHash={codeHash[:12]}… kind={codeKind}"
            )

    logger.info(
        f"[fix] repair done updated={updated} failed={failed} "
        f"missing={missing} skipped={skipped}"
    )
    if failed or missing:
        return 1
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="修复 license_codes.raw_code_signature 损坏行"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只扫描损坏行,不修改数据库",
    )
    parser.add_argument(
        "--repair",
        metavar="MAPPING.json",
        help="修复模式:读取 mapping JSON 并 UPDATE 损坏行",
    )
    parser.add_argument(
        "--kind",
        choices=["invite", "trial", "recharge", "activation"],
        help="仅处理指定 kind(过滤用)",
    )

    args = parser.parse_args()

    if args.dry_run and args.repair:
        logger.error("--dry-run 与 --repair 互斥")
        return 2
    if not args.dry_run and not args.repair:
        # 默认 dry-run
        args.dry_run = True

    if args.dry_run:
        return runDryRun(kind=args.kind)
    return runRepair(mappingPath=Path(args.repair))


if __name__ == "__main__":
    sys.exit(main())
