"""P0-A 开发期邮件发送占位实现；P1 替换为真实 SMTP/邮件服务。"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from app.config import getSettings


def sendPasswordResetEmail(email: str, rawToken: str, expiresAt: datetime) -> None:
    """开发环境打印一次性 token；非开发环境禁止输出原文。"""
    settings = getSettings()
    if settings.env.lower() in {"dev", "development", "test"}:
        logger.warning(f"[EmailStub] password reset email={email} token={rawToken} expiresAt={expiresAt.isoformat()}")
    else:
        logger.info(
            f"[EmailStub] password reset requested email={email} token=<redacted> expiresAt={expiresAt.isoformat()}"
        )


send_password_reset_email = sendPasswordResetEmail

__all__ = ["sendPasswordResetEmail", "send_password_reset_email"]
