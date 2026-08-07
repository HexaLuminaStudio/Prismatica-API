"""P0-A 订阅派发 cron(M8)。

执行时机:每小时一次(线上由 cron 容器触发)。
行为:
    1) 找到所有 current_period_end < NOW() 且 status='active' 的订阅:
       - 若 expires_at > NOW():推 next_period_start/end,派发新一轮 monthly_quota
       - 若 expires_at <= NOW():置 status='expired',把 user.tier 回 'free'
    2) 写 balance_ledger(grant entry)。
    3) 打印执行统计 + 把 summary 写入 stdout 供 cron 容器采集。

使用:
    python -m scripts.cron_subscriptions               # 立即跑一次
    python -m scripts.cron_subscriptions --dry-run     # 只统计不写
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

from app.config import getSettings  # noqa: E402
from app.db import getDb  # noqa: E402
from app.services.subscription_service import (  # noqa: E402
    expireSubscription,
    getActiveSubscription,
    getDueForRenewal,
    getExpiredCandidates,
    grantMonthlyQuota,
)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _advancePeriod(sub, now: datetime) -> datetime:
    """把订阅推进一个周期,返回新的 current_period_end(naive UTC)。"""
    periodDays = (sub.currentPeriodEnd - sub.currentPeriodStart).days or 30
    return now + timedelta(days=periodDays)


def runOnce(*, dryRun: bool = False) -> dict:
    """执行一次完整 cron 任务,返回统计 dict。"""
    settings = getSettings()
    stats = {
        "expiring": 0,  # 派发了新一期
        "expired": 0,    # 彻底过期
        "granted": 0,    # 总派发额
        "dryRun": dryRun,
        "startedAt": _now().isoformat(),
    }
    now = _now()

    # 2026-08-07:每次调用运行时再 import,以便测试可以 monkeypatch app.db.getDb
    from app.db import getDb as _getDb

    with _getDb() as db:
        # 1) 处理过期订阅(expires_at <= now)
        expired = getExpiredCandidates(db, before=now, limit=500)
        for sub in expired:
            if dryRun:
                stats["expired"] += 1
                continue
            try:
                expireSubscription(db, sub)
                stats["expired"] += 1
                logger.info(
                    f"[cron.subs] expired sub={sub.id} user={sub.userId} "
                    f"plan={sub.planCode}"
                )
            except Exception as e:
                logger.exception(f"[cron.subs] expire failed sub={sub.id}: {e}")
                db.rollback()

        # 2) 处理应续期的活跃订阅(next_grant_at <= now)
        due = getDueForRenewal(db, before=now, limit=500)
        for sub in due:
            # 防御性:可能刚才被 expire 了,跳过
            db.refresh(sub)
            if sub.status != "active":
                continue
            if sub.nextGrantAt is None or sub.nextGrantAt > now:
                continue
            if dryRun:
                stats["expiring"] += 1
                stats["granted"] += int(sub.monthlyQuota or 0)
                continue
            try:
                grant = grantMonthlyQuota(db, sub)
                stats["expiring"] += 1
                stats["granted"] += int(grant.grantedBalance or 0)
                # 推进周期
                newEnd = _advancePeriod(sub, now)
                sub.currentPeriodStart = now
                sub.currentPeriodEnd = newEnd
                sub.nextGrantAt = newEnd
                if sub.expiresAt < newEnd:
                    sub.expiresAt = newEnd
                db.commit()
                logger.info(
                    f"[cron.subs] renewed sub={sub.id} user={sub.userId} "
                    f"plan={sub.planCode} granted={grant.grantedBalance}"
                )
            except Exception as e:
                logger.exception(f"[cron.subs] renew failed sub={sub.id}: {e}")
                db.rollback()

    stats["finishedAt"] = _now().isoformat()
    logger.info(f"[cron.subs] done: {stats}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="P0-A 订阅派发 cron")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写")
    args = parser.parse_args()

    runOnce(dryRun=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
