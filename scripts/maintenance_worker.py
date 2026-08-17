"""周期运行预授权释放和订阅维护任务。"""
from __future__ import annotations

import os
import signal
import threading

from loguru import logger

from scripts.cron_preauth_release import runOnce as releasePreauths
from scripts.cron_subscriptions import runOnce as maintainSubscriptions


def _intervalSeconds() -> int:
    rawValue = os.getenv("MAINTENANCE_INTERVAL_SEC", "60")
    try:
        return max(15, min(3600, int(rawValue)))
    except ValueError:
        return 60


def main() -> int:
    stopEvent = threading.Event()

    def _stop(_signalNumber, _frame) -> None:
        stopEvent.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    intervalSeconds = _intervalSeconds()
    logger.info(f"[maintenance] 启动，执行间隔 {intervalSeconds} 秒")
    while not stopEvent.is_set():
        try:
            releasePreauths()
        except Exception as error:
            logger.exception(f"[maintenance] 预授权释放失败: {error}")
        try:
            maintainSubscriptions()
        except Exception as error:
            logger.exception(f"[maintenance] 订阅维护失败: {error}")
        stopEvent.wait(intervalSeconds)
    logger.info("[maintenance] 已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
