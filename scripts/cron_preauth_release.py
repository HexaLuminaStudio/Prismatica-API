"""P0-A preauth 超时释放 cron(M8)。

执行时机:每 1-5 分钟一次。
行为:
    - 找到所有 status='pending' 且 preauth_expires_at <= 当前 UTC 的 bill
    - 调用 billing_service.refund 释放冻结余额
    - 写 balance_ledger(refund entry)

使用:
    python -m scripts.cron_preauth_release
    python -m scripts.cron_preauth_release --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger  # noqa: E402

from app.services.billing_service import releaseExpiredPreauths  # noqa: E402


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def runOnce(*, dryRun: bool = False) -> dict:
    stats = {
        "released": 0,
        "skipped": 0,
        "dryRun": dryRun,
        "startedAt": _now().isoformat(),
    }
    # 2026-08-07:每次调用运行时再 import,以便测试可以 monkeypatch app.db.getDb
    from app.db import getDb as _getDb

    with _getDb() as db:
        try:
            stats["released"] = releaseExpiredPreauths(db, dryRun=dryRun)
        except Exception as error:
            logger.exception(f"[cron.preauth] release failed: {error}")
            db.rollback()
            stats["skipped"] += 1

    stats["finishedAt"] = _now().isoformat()
    logger.info(f"[cron.preauth] done: {stats}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-A preauth 超时释放 cron")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写")
    args = parser.parse_args()

    runOnce(dryRun=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
