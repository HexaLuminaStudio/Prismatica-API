"""P0-A audit_log 装饰器测试。

覆盖:
    - recordAudit 写库 + details 序列化
    - auditAction 装饰器在视图成功返回时自动写库
    - SENSITIVE_FIELDS(password / refreshToken 等)不出现在 details
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from flask import Flask
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import getSettings
from app.db import Base
from app.middleware.audit_log import (
    auditAction,
    installAuditContext,
    recordAudit,
)
from app.models.audit_log import AuditLog


@pytest.fixture()
def db() -> Iterator:
    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        yield s
    engine.dispose()


def testRecordAudit_WritesRow(db) -> None:
    row = recordAudit(
        db,
        actor="admin",
        action="user.ban",
        targetType="user",
        targetId="42",
        targetUser="42",
        details={"reason": "spam"},
        ip="127.0.0.1",
        requestId="request-42",
    )
    db.commit()
    assert row is not None
    audits = db.execute(select(AuditLog)).scalars().all()
    assert len(audits) == 1
    assert audits[0].action == "user.ban"
    assert audits[0].details == {"reason": "spam"}
    assert audits[0].requestId == "request-42"
    assert audits[0].createdAt is not None


def testRecordAudit_HandlesNonJsonableDetails(db) -> None:
    class Unserializable:
        def __repr__(self):
            return "<Unserializable>"

    recordAudit(
        db,
        actor="system",
        action="cron.tick",
        details={"obj": Unserializable(), "ok": 1},
    )
    db.commit()
    audit = db.execute(select(AuditLog)).scalars().one()
    assert "Unserializable" in str(audit.details)


def testRecordAudit_DoesNotRaiseOnDbError(db, monkeypatch) -> None:
    """审计失败不能影响主流程,只返回 None。"""
    from app.middleware import audit_log

    def _boom(*args, **kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(audit_log, "AuditLog", _boom)
    # 不会抛错,只返回 None
    row = recordAudit(db, actor="x", action="y")
    assert row is None


def testAuditAction_DecoratorWritesOnSuccess(monkeypatch) -> None:
    """装饰器形式的 audit:在视图成功返回时写一条 audit_log。"""
    # 用一个最小化的 Flask app + sqlite 走装饰器

    engine = create_engine("sqlite://", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # 同时 patch middleware.audit_log 引用的 getDb
    from app.middleware import audit_log as _audit_mw

    monkeypatch.setattr("app.db.getDb", _ctx)
    monkeypatch.setattr(_audit_mw, "getDb", _ctx)
    getSettings().autoInitSchema = False

    app = Flask(__name__)
    app.config["TESTING"] = True
    installAuditContext(app)

    from flask import g

    @app.post("/_test/hit")
    @auditAction("test.hit", targetUserFrom="g.userId")
    def _hit():
        g.userId = 99
        return {"ok": True}, 200

    @app.post("/_test/system")
    @auditAction("test.system", actorFrom=None, targetUserFrom=None)
    def _system():
        return {"ok": True}, 200

    client = app.test_client()
    resp = client.post(
        "/_test/hit",
        json={"password": "secret", "action": "test"},
        headers={"X-Forwarded-For": "10.0.0.1"},
    )
    assert resp.status_code == 200

    resp2 = client.post("/_test/system", json={})
    assert resp2.status_code == 200

    # 验证 audit_log 写入
    with factory() as s:
        audits = s.execute(select(AuditLog)).scalars().all()
        actions = [a.action for a in audits]
        assert "test.hit" in actions
        assert "test.system" in actions

        # password 不应该出现在 details
        hitAudit = next(a for a in audits if a.action == "test.hit")
        assert "password" not in (hitAudit.details or {})
        assert hitAudit.targetUser == "99"
        assert hitAudit.ip == "10.0.0.1"
        # system action 的 actor 是 'system'
        sysAudit = next(a for a in audits if a.action == "test.system")
        assert sysAudit.actor == "system"
