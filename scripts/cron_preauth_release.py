"""P0-A preauth 超时释放 cron(M8)。

执行时机:每 1-5 分钟一次。
行为:
    - 找到所有 status='pending' 且 created_at < NOW() - 5min 的 bill
    - 调用 billing_service.refund 释放冻结余额
    - 写 balance_ledger(refund entry)

使用:
    python -m scripts.cron_preauth_release
    python -m scripts.cron_preauth_release --dry-run
    python -m scripts.cron_preauth_release --older-than-minutes 10
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import getDb  # noqa: E402
from app.models.bill import Bill  # noqa: E402
from app.services.billing_service import refund as billingRefund  # noqa: E402


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def runOnce(*, olderThanMinutes: int = 5, dryRun: bool = False) -> dict:
    stats = {
        "released": 0,
        "skipped": 0,
        "dryRun": dryRun,
        "startedAt": _now().isoformat(),
    }
    cutoff = _now() - timedelta(minutes=olderThanMinutes)

    # 2026-08-07:每次调用运行时再 import,以便测试可以 monkeypatch app.db.getDb
    from app.db import getDb as _getDb

    with _getDb() as db:
        pending = db.execute(
            select(Bill)
            .where(Bill.status == "pending", Bill.createdAt < cutoff)
            .order_by(Bill.createdAt.asc())
            .limit(500)
        ).scalars().all()

        for bill in pending:
            if dryRun:
                stats["released"] += 1
                continue
            try:
                billingRefund(db, bill.billId)
                stats["released"] += 1
                logger.info(
                    f"[cron.preauth] refunded bill={bill.billId} "
                    f"user={bill.userId} cost={bill.estimatedCost}"
                )
            except Exception as e:
                logger.exception(f"[cron.preauth] refund failed bill={bill.billId}: {e}")
                db.rollback()
                stats["skipped"] += 1

    stats["finishedAt"] = _now().isoformat()
    logger.info(f"[cron.preauth] done: {stats}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-A preauth 超时释放 cron")
    parser.add_argument(
        "--older-than-minutes",
        type=int,
        default=5,
        help="释放多少分钟前 pending 的 bill(默认 5)",
    )
    parser.add_argument("--dry-run", action="store_true", help="只统计不写")
    args = parser.parse_args()

    runOnce(olderThanMinutes=args.older_than_minutes, dryRun=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
