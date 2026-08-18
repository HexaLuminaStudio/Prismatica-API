"""P0-A 订阅服务:createSubscription / getActive / list / grantMonthlyQuota。

设计要点:
    - 每月(30 天)为一个订阅周期;首期立即派发 monthly_quota,
      后续周期由 cron(脚本 scripts/cron_subscriptions.py)按 next_grant_at 触发派发
    - 派发与状态机迁移在单事务内完成,并写 balance_ledger + 可选 audit_log
    - 不会自动 renew(本轮没有支付通道);cron 看到 auto_renew=true 的过期订阅
      转 past_due,cron_renew 流程留 P1
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models.balance_ledger import BalanceLedger
from app.models.identity import IdentityBalance, User
from app.models.subscription import Subscription

# P0-A 默认 plan 目录(临时,后续可由 admin 后台配置)
PLAN_PRO_MONTHLY = "pro_monthly"  # 月度 Pro:30 天周期,monthly_quota=200
PLAN_TEAM_MONTHLY = "team_monthly"  # 团队版
PLAN_TRIAL = "trial"  # 试用:7 天,monthly_quota=20

PLAN_DEFAULT_PERIOD_DAYS = 30
PLAN_DEFAULT_TRIAL_DAYS = 7

PLAN_TABLE: dict[str, dict] = {
    PLAN_PRO_MONTHLY: {
        "displayName": "Pro 月度",
        "periodDays": 30,
        "monthlyQuota": 200,
        "tier": "pro",
    },
    PLAN_TEAM_MONTHLY: {
        "displayName": "Team 月度",
        "periodDays": 30,
        "monthlyQuota": 1000,
        "tier": "team",
    },
    PLAN_TRIAL: {
        "displayName": "试用",
        "periodDays": PLAN_DEFAULT_TRIAL_DAYS,
        "monthlyQuota": 20,
        "tier": "pro",  # 试用也享受 pro 功能
    },
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass(frozen=True)
class GrantResult:
    subscriptionId: int
    grantedBalance: int
    balanceAfter: int
    nextGrantAt: datetime | None


def listAllPlans() -> list[dict]:
    """公开 API:列出所有可用 plan。"""
    return [{"planCode": code, **meta} for code, meta in PLAN_TABLE.items()]


def resolvePlan(planCode: str) -> dict:
    plan = PLAN_TABLE.get(planCode)
    if plan is None:
        raise ApiError("BAD_REQUEST", f"未知订阅计划: {planCode}", httpStatus=400)
    return plan


# ---------------------------------------------------------------------------
# 创建 / 派发
# ---------------------------------------------------------------------------


def createSubscription(
    db: Session,
    userId: int,
    planCode: str,
    *,
    autoRenew: bool = False,
    startAt: datetime | None = None,
) -> tuple[Subscription, GrantResult]:
    """创建订阅并立即派发首期月度额度。

    - 自动取 plan_code 对应的元数据
    - started_at / current_period_start 取 startAt(默认 now)
    - current_period_end = start + periodDays
    - expires_at = current_period_end(P0-A 不支持 auto_renew 续期)
    - next_grant_at = current_period_end
    - monthly_quota 一次性写入 user_balance.balance + balance_ledger
    - 用户 tier 立即升到 plan 对应 tier
    """
    plan = resolvePlan(planCode)
    now = startAt or _now()
    periodDays = int(plan["periodDays"])
    monthlyQuota = int(plan["monthlyQuota"])
    tier = plan.get("tier", "free")
    periodEnd = now + timedelta(days=periodDays)

    user = db.execute(select(User).where(User.id == userId).with_for_update()).scalar_one_or_none()
    if user is None or user.deletedAt is not None:
        raise ApiError("NOT_FOUND", "用户不存在", httpStatus=404)
    if user.status != "active":
        raise ApiError("FORBIDDEN", "用户状态异常,无法订阅", httpStatus=403)

    sub = Subscription(
        userId=userId,
        planCode=planCode,
        status="active",
        startedAt=now,
        currentPeriodStart=now,
        currentPeriodEnd=periodEnd,
        expiresAt=periodEnd,
        nextGrantAt=periodEnd,
        autoRenew=autoRenew,
        monthlyQuota=monthlyQuota,
    )
    db.add(sub)
    db.flush()

    grantResult = _grantQuotaInternal(
        db,
        userId,
        amount=monthlyQuota,
        source="subscription_grant",
        refType="subscription",
        refId=str(sub.id),
        note=f"{planCode} 首次派发",
    )

    # 升级用户 tier
    user.tier = tier
    db.flush()

    return sub, grantResult


def grantMonthlyQuota(db: Session, subscription: Subscription) -> GrantResult:
    """为活跃订阅派发新一轮 monthly_quota(由 cron 调用)。"""
    if subscription.status != "active":
        raise ApiError("CONFLICT", f"订阅不在 active 状态: {subscription.status}", httpStatus=409)
    return _grantQuotaInternal(
        db,
        subscription.userId,
        amount=int(subscription.monthlyQuota or 0),
        source="subscription_grant",
        refType="subscription",
        refId=str(subscription.id),
        note=f"{subscription.planCode} 周期派发",
    )


def _grantQuotaInternal(
    db: Session,
    userId: int,
    *,
    amount: int,
    source: str,
    refType: str,
    refId: str,
    note: str,
) -> GrantResult:
    """单事务:balance += amount + balance_ledger 写入。"""
    if amount <= 0:
        raise ApiError("BAD_REQUEST", "派发额度必须为正数", httpStatus=400)

    balance = db.execute(
        select(IdentityBalance).where(IdentityBalance.userId == userId).with_for_update()
    ).scalar_one_or_none()
    if balance is None:
        # 用户没有 balance 行(老 redeem 路径),新建
        balance = IdentityBalance(userId=str(userId))
        db.add(balance)
        db.flush()
        balance = db.execute(
            select(IdentityBalance).where(IdentityBalance.userId == userId).with_for_update()
        ).scalar_one()
    balanceBefore = int(balance.balance or 0)
    balance.balance = balanceBefore + amount
    balance.lifetimeGrant = int(balance.lifetimeGrant or 0) + amount
    balance.version = int(balance.version or 0) + 1
    db.flush()
    balanceAfter = int(balance.balance)

    db.add(
        BalanceLedger(
            userId=userId,
            entryType="grant",
            amount=amount,
            balanceDelta=amount,
            reservedDelta=0,
            balanceAfter=balanceAfter,
            reservedAfter=int(balance.reserved or 0),
            source=source,
            refType=refType,
            refId=refId,
            note=note,
        )
    )
    db.flush()
    return GrantResult(
        subscriptionId=int(refId) if refType == "subscription" else 0,
        grantedBalance=amount,
        balanceAfter=balanceAfter,
        nextGrantAt=None,
    )


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def getActiveSubscription(db: Session, userId: int) -> Subscription | None:
    now = _now()
    return db.execute(
        select(Subscription)
        .where(
            Subscription.userId == userId,
            Subscription.status == "active",
            Subscription.expiresAt > now,
        )
        .order_by(Subscription.currentPeriodEnd.desc())
        .limit(1)
    ).scalar_one_or_none()


def listSubscriptions(
    db: Session,
    userId: int,
    *,
    limit: int = 50,
    cursorEnd: datetime | None = None,
) -> tuple[list[Subscription], datetime | None]:
    limit = max(1, min(200, limit))
    stmt = select(Subscription).where(Subscription.userId == userId)
    if cursorEnd is not None:
        stmt = stmt.where(Subscription.currentPeriodEnd < cursorEnd)
    stmt = stmt.order_by(Subscription.currentPeriodEnd.desc()).limit(limit + 1)
    rows = list(db.execute(stmt).scalars().all())
    nextCursor: datetime | None = None
    if len(rows) > limit:
        nextCursor = rows[limit - 1].currentPeriodEnd
        rows = rows[:limit]
    return rows, nextCursor


def getDueForRenewal(db: Session, *, before: datetime | None = None, limit: int = 100) -> list[Subscription]:
    """cron 用:获取需要续期/派发的活跃订阅(current_period_end <= before)。"""
    cutoff = before or _now()
    return list(
        db.execute(
            select(Subscription)
            .where(
                Subscription.status == "active",
                Subscription.nextGrantAt.isnot(None),
                Subscription.nextGrantAt <= cutoff,
            )
            .order_by(Subscription.nextGrantAt.asc())
            .limit(limit)
        ).scalars()
    )


def getExpiredCandidates(db: Session, *, before: datetime | None = None, limit: int = 100) -> list[Subscription]:
    """cron 用:获取应该转为 expired 的订阅(expires_at <= before 且仍 active)。"""
    cutoff = before or _now()
    return list(
        db.execute(
            select(Subscription)
            .where(
                Subscription.status == "active",
                Subscription.expiresAt <= cutoff,
            )
            .order_by(Subscription.expiresAt.asc())
            .limit(limit)
        ).scalars()
    )


def expireSubscription(db: Session, sub: Subscription) -> None:
    """订阅过期:status='expired',用户 tier 回 free。"""
    sub.status = "expired"
    sub.nextGrantAt = None
    user = db.get(User, sub.userId)
    if user is not None and user.tier != "free":
        user.tier = "free"
    db.flush()


# ---------------------------------------------------------------------------
# 兑换码升级辅助
# ---------------------------------------------------------------------------


def redeemInviteCode(
    db: Session,
    userId: int,
    grantedBalance: int,
    grantedDays: int,
    codeId: int,
    clientIp: str | None = None,
) -> tuple[Subscription | None, int]:
    """兑换 INV 码:升级到 Pro 月度订阅(若 grantedDays > 0),并派发 quota。

    返回: (subscription_or_None, grantedBalance)
        - 若 grantedDays > 0: 创建 PLAN_PRO_MONTHLY,周期 = grantedDays
        - 若 grantedDays == 0: 不创建订阅,仅 grant 余额
    """
    if grantedDays > 0:
        now = _now()
        sub = Subscription(
            userId=userId,
            planCode=PLAN_PRO_MONTHLY,
            status="active",
            startedAt=now,
            currentPeriodStart=now,
            currentPeriodEnd=now + timedelta(days=grantedDays),
            expiresAt=now + timedelta(days=grantedDays),
            nextGrantAt=now + timedelta(days=grantedDays),
            autoRenew=False,
            monthlyQuota=grantedBalance,
        )
        db.add(sub)
        db.flush()

        if grantedBalance > 0:
            _grantQuotaInternal(
                db,
                userId,
                amount=grantedBalance,
                source="invite_grant",
                refType="code",
                refId=str(codeId),
                note=f"INV 码兑换(grantedDays={grantedDays})",
            )
        return sub, grantedBalance

    # grantedDays == 0:仅 grant 余额,不创建订阅
    if grantedBalance > 0:
        _grantQuotaInternal(
            db,
            userId,
            amount=grantedBalance,
            source="invite_grant",
            refType="code",
            refId=str(codeId),
            note="INV 码兑换(无周期)",
        )
    return None, grantedBalance


def redeemTrialCode(
    db: Session,
    userId: int,
    grantedBalance: int,
    grantedDays: int,
    codeId: int,
) -> Subscription:
    """兑换 TRY 码:创建 trial 订阅。"""
    days = grantedDays or PLAN_DEFAULT_TRIAL_DAYS
    now = _now()
    periodEnd = now + timedelta(days=days)
    sub = Subscription(
        userId=userId,
        planCode=PLAN_TRIAL,
        status="active",
        startedAt=now,
        currentPeriodStart=now,
        currentPeriodEnd=periodEnd,
        expiresAt=periodEnd,
        nextGrantAt=periodEnd,
        autoRenew=False,
        monthlyQuota=grantedBalance,
    )
    db.add(sub)
    db.flush()
    if grantedBalance > 0:
        _grantQuotaInternal(
            db,
            userId,
            amount=grantedBalance,
            source="trial_grant",
            refType="code",
            refId=str(codeId),
            note=f"TRY 码兑换(days={days})",
        )
    return sub


def redeemRechargeCode(
    db: Session,
    userId: int,
    amount: int,
    codeId: int,
) -> int:
    """兑换 RCH 码:仅加余额。"""
    if amount <= 0:
        raise ApiError("BAD_REQUEST", "充值金额必须为正数", httpStatus=400)
    result = _grantQuotaInternal(
        db,
        userId,
        amount=amount,
        source="recharge_code",
        refType="code",
        refId=str(codeId),
        note="RCH 码兑换",
    )
    return result.grantedBalance


# ---------------------------------------------------------------------------
# snake_case 别名(供模块外统一引用)
# ---------------------------------------------------------------------------

create_subscription = createSubscription
get_active_subscription = getActiveSubscription
list_subscriptions = listSubscriptions
grant_monthly_quota = grantMonthlyQuota
get_due_for_renewal = getDueForRenewal
get_expired_candidates = getExpiredCandidates
expire_subscription = expireSubscription
redeem_invite_code = redeemInviteCode
redeem_trial_code = redeemTrialCode
redeem_recharge_code = redeemRechargeCode
list_all_plans = listAllPlans
resolve_plan = resolvePlan


__all__ = [
    "PLAN_PRO_MONTHLY",
    "PLAN_TEAM_MONTHLY",
    "PLAN_TRIAL",
    "PLAN_TABLE",
    "GrantResult",
    "createSubscription",
    "grantMonthlyQuota",
    "getActiveSubscription",
    "listSubscriptions",
    "getDueForRenewal",
    "getExpiredCandidates",
    "expireSubscription",
    "redeemInviteCode",
    "redeemTrialCode",
    "redeemRechargeCode",
    "listAllPlans",
    "resolvePlan",
]
