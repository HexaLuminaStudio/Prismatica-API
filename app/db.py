"""SQLAlchemy 2.x 同步引擎 + Session。

- 使用 PyMySQL 驱动
- 启用连接池回收(防止 MySQL wait_timeout 断连)
- 提供 getDb() 上下文管理器(自动 commit/rollback/close)
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import getSettings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""

    pass


_settings = getSettings()
engine: Engine = create_engine(
    _settings.dbUrl,
    pool_size=_settings.dbPoolSize,
    max_overflow=_settings.dbMaxOverflow,
    pool_recycle=_settings.dbPoolRecycleSec,
    pool_timeout=_settings.dbPoolTimeoutSec,
    pool_pre_ping=True,
    pool_use_lifo=True,
    connect_args={
        "connect_timeout": _settings.dbConnectTimeoutSec,
        "read_timeout": _settings.dbReadTimeoutSec,
        "write_timeout": _settings.dbWriteTimeoutSec,
    },
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


@contextmanager
def getDb() -> Iterator[Session]:
    """事务化的 Session 上下文(自动 commit/rollback/close)。

    用法:
        with getDb() as db:
            db.add(...)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def pingDb() -> bool:
    """健康检查:是否能 ping 通 MySQL。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def initSchemaFromSql(sqlPath: str) -> None:
    """启动时执行 schema.sql(开发期便利,生产建议用 Alembic)。

    使用 INSERT IGNORE 等幂等语句;若失败则抛出,由调用方决定是否阻断启动。
    """
    from pathlib import Path

    from loguru import logger

    sqlText = Path(sqlPath).read_text(encoding="utf-8")
    # 拆分语句(以 ; 结尾,忽略空行/注释)
    statements: list[str] = []
    buf: list[str] = []
    for line in sqlText.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    if buf:
        statements.append("\n".join(buf))

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    logger.info(f"[DB] schema 初始化完成: {len(statements)} 条语句")
