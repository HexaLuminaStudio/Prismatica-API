"""管理后台价格版本发布服务。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models.pricing import PricingVersion
from app.services import admin_pricing_service as service
from app.services.pricing import getPricingService


@pytest.fixture()
def pricingDb(monkeypatch) -> Iterator[sessionmaker[Session]]:
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def getTestDb():
        with factory() as session:
            yield session

    monkeypatch.setattr(service, "getDb", getTestDb)
    yield factory
    engine.dispose()


def _rules(fixedCost: int) -> list[dict]:
    return [
        {
            "featureCode": "analysis_export",
            "displayName": "语料分析导出",
            "billingMode": "fixed",
            "unitName": "次",
            "fixedCost": fixedCost,
            "minCost": 0,
            "maxCost": 1_000_000,
            "enabled": True,
        },
        {
            "featureCode": "ai_chat",
            "displayName": "AI 聊天",
            "billingMode": "token",
            "unitName": "Token",
            "unitSize": 1_000_000,
            "inputTokenCostPerUnit": 2,
            "outputTokenCostPerUnit": 3,
            "minCost": 1,
            "maxCost": 1_000_000,
            "enabled": True,
        },
    ]


def testPublishPricingVersion_ArchivesOldAndActivatesNew(pricingDb) -> None:
    first = service.createPricingDraft("root", _rules(5), "首次发布")
    service.publishPricingVersion(first["versionCode"], "root")
    second = service.createPricingDraft("root", _rules(8), "导出调价")
    service.publishPricingVersion(second["versionCode"], "root")

    overview = service.getPricingOverview()
    assert overview["activeVersion"] == second["versionCode"]
    assert next(rule for rule in overview["rules"] if rule["featureCode"] == "analysis_export")["fixedCost"] == 8
    with pricingDb() as db:
        versions = db.execute(select(PricingVersion).order_by(PricingVersion.versionId)).scalars().all()
        assert [version.status for version in versions] == ["retired", "published"]


def testPublishedPricingCatalog_ExposesCurrentStatus(pricingDb) -> None:
    draft = service.createPricingDraft("root", _rules(9), "状态页测试")
    service.publishPricingVersion(draft["versionCode"], "root")

    with pricingDb() as db:
        catalog = getPricingService().publicCatalog(db)

    assert catalog["version"] == draft["versionCode"]
    assert catalog["state"] == "active"
    assert catalog["source"] == "published"
    assert catalog["effectiveAt"]
    assert next(rule for rule in catalog["rules"] if rule["featureCode"] == "analysis_export")["fixedCost"] == 9
    aiRule = next(rule for rule in catalog["rules"] if rule["featureCode"] == "ai_chat")
    assert aiRule["unitSize"] == 1_000_000
    assert aiRule["inputTokenCostPerUnit"] == 2
    assert aiRule["outputTokenCostPerUnit"] == 3


def testFixedDraft_NormalizesMinimumAndMaximum(pricingDb) -> None:
    result = service.createPricingDraft("root", _rules(7), "fixed")
    fixed = next(rule for rule in result["rules"] if rule["featureCode"] == "analysis_export")
    assert fixed["minCost"] == 7
    assert fixed["maxCost"] == 7
