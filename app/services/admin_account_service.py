"""Admin 账号管理服务(2026-08-06 M3 新增):

- listAdmins(limit, cursor, q, status, role) → (items, nextCursor)
- createAdmin(username, password, role, actor) → dict
- setAdminStatus(userId, status, actor) → dict
- updateAdminRole(userId, role, actor) → dict
- resetAdminPassword(userId, actor) → {userId, newPassword}
- softDeleteAdmin(userId, actor, confirmUsername) → dict

约束:
    - 软删:username 永久占用;deleted_at 非空即不可见
    - 至少保留一个 active owner(锁定/降级/软删时校验)
    - 不能软删自己
    - owner / admin 是仅有的合法 role 值
    - 重置密码 → 写 pwd_reset_at,旧 cookie 在下一次请求被 401

所有写操作走 recordAudit(...)。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from sqlalchemy import func as saFunc
from sqlalchemy import or_, select

from app.db import getDb
from app.errors import ApiError
from app.models import AdminUser
from app.security.password import hashPassword
from app.services.admin_audit_service import recordAudit

VALID_ROLES = ("owner", "admin")
VALID_STATUSES = ("active", "locked")


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """UTC naive(对齐 DB 字段类型,server_default=func.current_timestamp)。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _countActiveOwners(db) -> int:
    """统计 active 且未软删的 owner 数量。"""
    return int(
        db.execute(
            select(saFunc.count())
            .select_from(AdminUser)
            .where(
                AdminUser.role == "owner",
                AdminUser.status == "active",
                AdminUser.deletedAt.is_(None),
            )
        ).scalar_one()
        or 0
    )


def _ensureAtLeastOneActiveOwner(
    db,
    *,
    actorUserId: str,
    targetUserId: str,
    targetRole: str,
    newStatus: str | None = None,
) -> None:
    """在 setStatus / updateRole / softDelete 前调用,确保仍至少保留一个 active owner。

    - 若目标 role != owner → 不影响 owner 计数,直接通过。
    - 若目标 role == owner 且要把 status 改为 locked 或删除 → 拒绝(剩余 0 owner)。
    """
    if targetRole != "owner":
        return
    # 当前是否唯一 active owner
    target = db.get(AdminUser, targetUserId)
    if target is None or target.deletedAt is not None:
        raise ApiError("NOT_FOUND", "管理员账号不存在或已删除")
    # 推导该操作完成后,目标 owner 是否仍为 active owner
    finalStatus = newStatus if newStatus is not None else target.status
    willRemainActiveOwner = finalStatus == "active"
    if not willRemainActiveOwner:
        # 统计其他 active owner(排除自己)
        otherActive = (
            db.execute(
                select(saFunc.count())
                .select_from(AdminUser)
                .where(
                    AdminUser.role == "owner",
                    AdminUser.status == "active",
                    AdminUser.deletedAt.is_(None),
                    AdminUser.userId != targetUserId,
                )
            ).scalar_one()
            or 0
        )
        if otherActive < 1:
            raise ApiError(
                "FORBIDDEN",
                "系统至少需要保留一个 active owner,该操作会清零",
                httpStatus=403,
            )


def _parseCursor(cursor: str | None) -> datetime | None:
    if not cursor:
        return None
    try:
        return datetime.fromisoformat(cursor.replace("Z", "+00:00"))
    except ValueError as e:
        raise ApiError("BAD_REQUEST", f"cursor 格式错误: {e}") from e


# ---------------------------------------------------------------------------
# 列表 / 详情
# ---------------------------------------------------------------------------


def listAdmins(
    limit: int = 50,
    cursor: str | None = None,
    q: str | None = None,
    status: str | None = None,
    role: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """账号列表(过滤软删)。"""
    limit = max(1, min(200, limit))
    if status and status not in VALID_STATUSES:
        raise ApiError("BAD_REQUEST", f"status 必须为 {VALID_STATUSES}")
    if role and role not in VALID_ROLES:
        raise ApiError("BAD_REQUEST", f"role 必须为 {VALID_ROLES}")

    with getDb() as db:
        stmt = select(AdminUser).where(AdminUser.deletedAt.is_(None))
        cursorDt = _parseCursor(cursor)
        if cursorDt is not None:
            stmt = stmt.where(AdminUser.createdAt < cursorDt)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    AdminUser.username.like(like),
                    AdminUser.userId.like(like),
                )
            )
        if status:
            stmt = stmt.where(AdminUser.status == status)
        if role:
            stmt = stmt.where(AdminUser.role == role)

        stmt = stmt.order_by(AdminUser.createdAt.desc()).limit(limit + 1)
        rows = db.execute(stmt).scalars().all()

        nextCursor: str | None = None
        if len(rows) > limit:
            nextCursor = rows[limit - 1].createdAt.isoformat()
            rows = rows[:limit]

        items = [
            {
                "userId": r.userId,
                "username": r.username,
                "role": r.role,
                "status": r.status,
                "lastLoginAt": r.lastLoginAt,
                "failedAttempts": int(r.failedAttempts or 0),
                "createdAt": r.createdAt,
            }
            for r in rows
        ]
        return items, nextCursor


# ---------------------------------------------------------------------------
# 创建
# ---------------------------------------------------------------------------


def createAdmin(
    *,
    username: str,
    password: str,
    role: str = "admin",
    actor: str,
    actorUserId: str | None = None,
) -> dict[str, Any]:
    """创建新管理员账号。

    约束:
        - username 长度 3~64(由 Pydantic schema 校验,这里再防御一次)
        - password 长度 ≥ 8(同上)
        - role ∈ {owner, admin}
        - username 唯一(包括软删占用的)
    """
    if role not in VALID_ROLES:
        raise ApiError("BAD_REQUEST", f"role 必须为 {VALID_ROLES}")
    if len(username) < 3 or len(username) > 64:
        raise ApiError("BAD_REQUEST", "username 长度必须 3~64")
    if len(password) < 8:
        raise ApiError("BAD_REQUEST", "password 长度必须 ≥ 8")

    with getDb() as db:
        # username 唯一(含软删)
        dup = (
            db.query(AdminUser)
            .filter(AdminUser.username == username)
            .one_or_none()
        )
        if dup is not None:
            raise ApiError("USERNAME_TAKEN", "username 已被使用(含已软删)", httpStatus=409)

        admin = AdminUser(
            userId="adm_" + secrets.token_hex(16),
            username=username,
            passwordHash=hashPassword(password),
            role=role,
            status="active",
            failedAttempts=0,
        )
        db.add(admin)
        db.flush()

        result = {
            "userId": admin.userId,
            "username": admin.username,
            "role": admin.role,
            "status": admin.status,
            "createdAt": admin.createdAt,
        }

    recordAudit(
        actor=actor,
        action="admin.create_admin",
        targetUser=result["userId"],
        details={"username": username, "role": role},
    )
    if role == "owner":
        logger.warning(
            f"[AdminAccount] owner 角色创建新 admin,actor={actor} "
            f"newUserId={result['userId']} username={username}"
        )
    return result


# ---------------------------------------------------------------------------
# 改 status
# ---------------------------------------------------------------------------


def setAdminStatus(
    *,
    userId: str,
    status: str,
    actor: str,
    actorUserId: str | None = None,
) -> dict[str, Any]:
    """锁定 / 解锁账号(active ⇄ locked)。"""
    if status not in VALID_STATUSES:
        raise ApiError("BAD_REQUEST", f"status 必须为 {VALID_STATUSES}")

    with getDb() as db:
        target = db.get(AdminUser, userId)
        if target is None or target.deletedAt is not None:
            raise ApiError("NOT_FOUND", "管理员账号不存在或已删除")

        if status == "locked":
            _ensureAtLeastOneActiveOwner(
                db,
                actorUserId=actorUserId or "",
                targetUserId=userId,
                targetRole=target.role,
                newStatus="locked",
            )

        oldStatus = target.status
        target.status = status
        # 解锁时清零失败计数;锁定时不清(等下次登录校验)
        if status == "active":
            target.failedAttempts = 0
        db.commit()
        username = target.username

    recordAudit(
        actor=actor,
        action="admin.set_admin_status",
        targetUser=userId,
        details={"username": username, "oldStatus": oldStatus, "newStatus": status},
    )
    return {"userId": userId, "role": None, "status": status}


# ---------------------------------------------------------------------------
# 改 role
# ---------------------------------------------------------------------------


def updateAdminRole(
    *,
    userId: str,
    role: str,
    actor: str,
    actorUserId: str | None = None,
) -> dict[str, Any]:
    """调整角色(owner ⇄ admin)。"""
    if role not in VALID_ROLES:
        raise ApiError("BAD_REQUEST", f"role 必须为 {VALID_ROLES}")

    with getDb() as db:
        target = db.get(AdminUser, userId)
        if target is None or target.deletedAt is not None:
            raise ApiError("NOT_FOUND", "管理员账号不存在或已删除")

        oldRole = target.role
        if oldRole == role:
            return {"userId": userId, "role": role, "status": target.status}

        # admin → owner:无需校验
        # owner → admin:确保还有其他 active owner
        if oldRole == "owner" and role == "admin":
            otherActive = (
                db.execute(
                    select(saFunc.count())
                    .select_from(AdminUser)
                    .where(
                        AdminUser.role == "owner",
                        AdminUser.status == "active",
                        AdminUser.deletedAt.is_(None),
                        AdminUser.userId != userId,
                    )
                ).scalar_one()
                or 0
            )
            if otherActive < 1:
                raise ApiError(
                    "FORBIDDEN",
                    "系统至少需要保留一个 owner,该操作会清零",
                    httpStatus=403,
                )

        target.role = role
        db.commit()
        username = target.username

    recordAudit(
        actor=actor,
        action="admin.update_admin_role",
        targetUser=userId,
        details={"username": username, "oldRole": oldRole, "newRole": role},
    )
    return {"userId": userId, "role": role, "status": None}


# ---------------------------------------------------------------------------
# 重置密码
# ---------------------------------------------------------------------------


def resetAdminPassword(
    *,
    userId: str,
    actor: str,
    actorUserId: str | None = None,
) -> dict[str, Any]:
    """重置某账号密码(返回一次性明文)。"""
    newPassword = secrets.token_urlsafe(16)
    now = _utcnow()
    with getDb() as db:
        target = db.get(AdminUser, userId)
        if target is None or target.deletedAt is not None:
            raise ApiError("NOT_FOUND", "管理员账号不存在或已删除")

        target.passwordHash = hashPassword(newPassword)
        target.pwdResetAt = now
        target.failedAttempts = 0
        db.commit()
        username = target.username

    recordAudit(
        actor=actor,
        action="admin.reset_admin_password",
        targetUser=userId,
        details={"username": username},
        # 注意:不写明文密码到 audit
    )
    return {"userId": userId, "newPassword": newPassword}


# ---------------------------------------------------------------------------
# 软删除
# ---------------------------------------------------------------------------


def softDeleteAdmin(
    *,
    userId: str,
    actor: str,
    actorUserId: str | None = None,
    confirmUsername: str = "",
) -> dict[str, Any]:
    """软删除账号。

    校验:
        - actorUserId != userId(不能删除自己)
        - confirmUsername == target.username(二次确认)
        - 删除后仍至少一个 active owner
    """
    with getDb() as db:
        target = db.get(AdminUser, userId)
        if target is None or target.deletedAt is not None:
            raise ApiError("NOT_FOUND", "管理员账号不存在或已删除")

        if actorUserId and actorUserId == userId:
            raise ApiError("FORBIDDEN", "不能删除自己", httpStatus=403)

        if confirmUsername.strip() != target.username:
            raise ApiError(
                "BAD_REQUEST",
                "确认 username 与目标不匹配,请重新输入",
                httpStatus=400,
            )

        # 校验 owner 计数
        _ensureAtLeastOneActiveOwner(
            db,
            actorUserId=actorUserId or "",
            targetUserId=userId,
            targetRole=target.role,
            newStatus="deleted",
        )

        now = _utcnow()
        target.deletedAt = now
        # 同时强制 pwdResetAt:让已登录的该账号 session 立刻失效
        target.pwdResetAt = now
        db.commit()
        username = target.username

    recordAudit(
        actor=actor,
        action="admin.soft_delete_admin",
        targetUser=userId,
        details={"username": username},
    )
    return {"userId": userId, "deletedAt": now}


__all__ = [
    "VALID_ROLES",
    "VALID_STATUSES",
    "listAdmins",
    "createAdmin",
    "setAdminStatus",
    "updateAdminRole",
    "resetAdminPassword",
    "softDeleteAdmin",
]
