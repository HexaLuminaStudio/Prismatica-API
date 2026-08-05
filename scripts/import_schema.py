# coding: utf-8
"""手动把 scripts/schema.sql 灌入 six_corpus(开发库已存在,跳过 CREATE DATABASE/USE)。

执行:
    uv run python scripts/import_schema.py
"""
from __future__ import annotations

from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parent.parent
DB_HOST = "mysql3.sqlpub.com"
DB_PORT = 3308
DB_USER = "hungry630"
DB_PASSWORD = "KaTYD6ohJxUnfRq7"
DB_NAME = "six_corpus"

# 1) 连接(不指定库),先确保库存在 + utf8mb4
admin = pymysql.connect(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASSWORD,
    charset="utf8mb4",
    connect_timeout=10,
)
with admin.cursor() as cur:
    cur.execute(
        f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
        f"DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci"
    )
    print(f"[ok] database ready: {DB_NAME}")
admin.close()

# 2) 重新连到目标库,执行 schema.sql
conn = pymysql.connect(
    host=DB_HOST,
    port=DB_PORT,
    user=DB_USER,
    password=DB_PASSWORD,
    database=DB_NAME,
    charset="utf8mb4",
    connect_timeout=10,
)
sqlText = (ROOT / "scripts" / "schema.sql").read_text(encoding="utf-8")

# 拆分语句(以分号结尾,忽略注释行)
statements: list[str] = []
buf: list[str] = []
for rawLine in sqlText.splitlines():
    line = rawLine.rstrip()
    stripped = line.strip()
    if not stripped or stripped.startswith("--"):
        continue
    buf.append(line)
    if stripped.endswith(";"):
        statements.append("\n".join(buf).rstrip(";").strip())
        buf = []
if buf:
    statements.append("\n".join(buf).rstrip(";").strip())

print(f"[info] {len(statements)} statements to execute")

with conn.cursor() as cur:
    for i, stmt in enumerate(statements, 1):
        try:
            cur.execute(stmt)
        except Exception as e:
            print(f"[FAIL] stmt #{i}: {e}")
            print("----")
            print(stmt[:300])
            print("----")
            raise

conn.commit()

with conn.cursor() as cur:
    cur.execute("SHOW TABLES")
    tables = [r[0] for r in cur.fetchall()]
    print(f"[ok] tables after import: {tables}")
conn.close()
print("[done] schema imported.")