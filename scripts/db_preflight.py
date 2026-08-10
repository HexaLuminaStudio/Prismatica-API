"""只读数据库预检：验证连接并报告账号计费 P0 的 schema 缺口。

用法（项目根目录）：
    python -m scripts.db_preflight

脚本只读取 information_schema，不创建、修改或删除任何数据库对象，也不会
输出主机、账号、密码等连接凭据。
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Set
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402

P0_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "users": {
        "id",
        "email",
        "password_hash",
        "tier",
        "status",
        "failed_login_count",
        "locked_until",
        "email_verified",
        "deleted_at",
    },
    "user_devices": {
        "id",
        "device_id",
        "user_id",
        "device_name",
        "platform",
        "last_seen_at",
        "status",
    },
    "subscriptions": {
        "id",
        "user_id",
        "plan_code",
        "status",
        "started_at",
        "current_period_start",
        "current_period_end",
        "auto_renew",
        "monthly_quota",
    },
    "user_balance": {
        "user_id",
        "balance",
        "reserved",
        "lifetime_grant",
        "lifetime_consumed",
    },
    "balance_ledger": {
        "id",
        "user_id",
        "entry_type",
        "amount",
        "source",
        "ref_id",
        "note",
        "created_at",
    },
    "password_reset_tokens": {
        "id",
        "user_id",
        "token_hash",
        "expires_at",
        "used_at",
        "created_at",
    },
    "refresh_tokens": {"id", "jti", "token_hash", "user_id", "device_id", "expires_at", "revoked_at"},
    "revoked_tokens": {"jti", "user_id", "token_type", "expires_at", "revoked_at"},
    "idempotency_keys": {
        "id",
        "user_id",
        "operation",
        "idempotency_key",
        "request_hash",
        "expires_at",
    },
    "license_codes": {"id", "code_hash", "code_kind", "status", "max_uses", "used_count"},
    "code_redemptions": {"id", "code_id", "user_id", "redeemed_at"},
    "bills": {
        "id",
        "bill_id",
        "user_id",
        "feature",
        "estimated_cost",
        "actual_cost",
        "status",
        "idempotency_key",
        "request_hash",
        "description",
        "pricing_version",
        "pricing_snapshot",
        "input_tokens",
        "output_tokens",
        "preauth_expires_at",
        "settled_at",
        "refunded_at",
    },
    "pricing_versions": {
        "version_id",
        "version_code",
        "status",
        "created_by",
        "published_at",
    },
    "pricing_rules": {
        "rule_id",
        "version_id",
        "feature_code",
        "billing_mode",
        "unit_name",
        "unit_size",
        "per_unit_cost",
        "min_cost",
        "max_cost",
        "enabled",
    },
    "admin_users": {"user_id", "username", "password_hash", "role", "status"},
    "audit_logs": {"audit_id", "actor_type", "actor", "action", "request_id", "created_at"},
}


def findSchemaGaps(
    actual: Mapping[str, Set[str]],
    required: Mapping[str, Set[str]] = P0_REQUIRED_COLUMNS,
) -> dict[str, list[str]]:
    """返回缺失表/字段，便于单测且不依赖数据库连接。"""
    gaps: dict[str, list[str]] = {}
    for table, requiredColumns in required.items():
        actualColumns = actual.get(table)
        if actualColumns is None:
            gaps[table] = ["<table missing>"]
            continue
        missing = sorted(requiredColumns - set(actualColumns))
        if missing:
            gaps[table] = missing
    return gaps


def inspectDatabase(settings: Settings) -> tuple[str, dict[str, set[str]]]:
    """以只读事务读取服务端版本和目标库字段清单。"""
    connection = pymysql.connect(
        host=settings.dbHost,
        port=settings.dbPort,
        user=settings.dbUser,
        password=settings.dbPassword,
        database=settings.dbName,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=20,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            cursor.execute("SELECT VERSION()")
            version = str(cursor.fetchone()[0])
            cursor.execute(
                "SELECT table_name, column_name "
                "FROM information_schema.columns "
                "WHERE table_schema = %s "
                "ORDER BY table_name, ordinal_position",
                (settings.dbName,),
            )
            schema: dict[str, set[str]] = {}
            for table, column in cursor.fetchall():
                schema.setdefault(str(table), set()).add(str(column))
            return version, schema
    finally:
        connection.close()


def main() -> int:
    settings = Settings(_env_file=ROOT / ".env")
    try:
        version, schema = inspectDatabase(settings)
    except Exception as error:  # noqa: BLE001
        print(f"[FAIL] 数据库只读预检失败：{type(error).__name__}")
        return 1

    print(f"[OK] 数据库连接成功；MySQL {version}；发现 {len(schema)} 张表")
    gaps = findSchemaGaps(schema)
    if not gaps:
        print("[OK] 账号计费 P0 必需表与字段已就绪")
        return 0

    print("[WARN] 账号计费 P0 schema 尚未就绪：")
    for table, missing in gaps.items():
        print(f"  - {table}: {', '.join(missing)}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
