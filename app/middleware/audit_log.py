"""P0-A 审计日志中间件 / 装饰器。

提供:
    - `recordAudit(db, actor, action, targetUser=..., details=..., ip=...)` — 显式写一条
    - `auditAction(action, targetUserFrom='g.userId', targetType='user')` — 装饰器,
      在视图函数返回前自动写 audit_log(成功时)。
    - `installAuditContext(app)` — 注册 Flask `after_request`,在请求结束时把
      `flask.g.auditEvents` 收集到的事件一次性入库(供多步事务的视图)。

设计要点:
    - 审计行作为安全/合规事实表,不允许修改
    - actor 默认为 'system' 或 admin username;targetUser 为被操作用户 id(若适用)
    - IP 优先 X-Forwarded-For(代理链第一跳)
"""
from __future__ import annotations

import json
from functools import wraps
from typing import Any, Callable

from flask import Flask, g, request
from loguru import logger
from sqlalchemy.orm import Session

from app.db import getDb
from app.models.audit_log import AuditLog


def _toJsonable(value: Any) -> Any:
    """递归把 value 转为可 JSON 序列化的形式;不可序列化的子元素转 str。"""
    if isinstance(value, dict):
        return {k: _toJsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_toJsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return str(value)


def getClientIp() -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr


def recordAudit(
    db: Session,
    *,
    actor: str,
    action: str,
    targetType: str | None = None,
    targetId: str | None = None,
    targetUser: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
    requestId: str | None = None,
) -> AuditLog:
    """单条审计写入(写库不抛错,失败仅日志)。"""
    try:
        cleanDetails = _toJsonable(details) if details else None
        # 二次防御:即使 _toJsonable 漏掉,这里兜底 ensure-ascii + default=str
        if cleanDetails is not None:
            try:
                json.dumps(cleanDetails, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                cleanDetails = json.loads(json.dumps(cleanDetails, default=str))
        row = AuditLog(
            actor=actor or "system",
            action=action,
            targetType=targetType,
            targetId=targetId,
            targetUser=targetUser,
            details=cleanDetails,
            ip=ip,
        )
        db.add(row)
        db.flush()
        return row
    except Exception as exc:
        logger.warning(f"[Audit] record failed action={action}: {exc}")
        if db:
            db.rollback()
        # 审计失败不能影响主流程
        return None  # type: ignore[return-value]


def auditAction(
    action: str,
    *,
    actorFrom: str = "g.userId",
    targetUserFrom: str = "g.userId",
    targetType: str = "user",
    includeDetails: bool = True,
) -> Callable:
    """装饰器:视图成功返回(2xx)后写一条 audit。

    Args:
        action:审计动作名,如 'user.register' / 'user.login'
        actorFrom:从 flask.g 哪个属性取 actor。None 表示 'system'
        targetUserFrom:从 flask.g 哪个属性取 target_user
        targetType:目标类型字符串
        includeDetails:是否把 request.json 写入 details(去敏感字段)
    """
    SENSITIVE_FIELDS = {"password", "newPassword", "oldPassword", "refreshToken", "accessToken"}

    def _resolve(value: str | None) -> Any:
        if value is None:
            return None
        if value == "g.userId":
            return getattr(g, "userId", None)
        if value == "g.adminUsername":
            return getattr(g, "adminUsername", None)
        if value.startswith("g."):
            return getattr(g, value[2:], None)
        return None

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            response = func(*args, **kwargs)
            try:
                actorVal = _resolve(actorFrom)
                targetVal = _resolve(targetUserFrom)
                details: dict[str, Any] = {}
                if includeDetails:
                    body = request.get_json(silent=True) or {}
                    if isinstance(body, dict):
                        details = {k: v for k, v in body.items() if k not in SENSITIVE_FIELDS}
                    details.setdefault("method", request.method)
                    details.setdefault("path", request.path)
                actor = str(actorVal) if actorVal is not None else "system"
                # 单独事务写入,不影响主请求
                with getDb() as db:
                    recordAudit(
                        db,
                        actor=actor,
                        action=action,
                        targetType=targetType,
                        targetId=str(targetVal) if targetVal is not None else None,
                        targetUser=str(targetVal) if targetVal is not None else None,
                        details=details or None,
                        ip=getClientIp(),
                        requestId=getattr(g, "requestId", None),
                    )
            except Exception as exc:
                logger.warning(f"[Audit] decorator write failed for action={action}: {exc}")
            return response

        return wrapper

    return decorator


def installAuditContext(app: Flask) -> None:
    """在请求结束时把 g.auditEvents 写库(若视图手动 push 的话)。

    大多数装饰器已直接写库,这里只保留一个空 hook 给未来批量事件使用。
    """

    @app.after_request
    def _flushAuditEvents(response):
        events = getattr(g, "auditEvents", None) or []
        if not events:
            return response
        try:
            with getDb() as db:
                for event in events:
                    recordAudit(
                        db,
                        actor=event.get("actor", "system"),
                        action=event["action"],
                        targetType=event.get("targetType"),
                        targetId=event.get("targetId"),
                        targetUser=event.get("targetUser"),
                        details=event.get("details"),
                        ip=event.get("ip") or getClientIp(),
                    )
        except Exception as exc:
            logger.warning(f"[Audit] flush events failed: {exc}")
        return response


__all__ = [
    "getClientIp",
    "recordAudit",
    "auditAction",
    "installAuditContext",
]
