"""管理后台版本化定价服务。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db import getDb
from app.errors import ApiError
from app.models.pricing import PricingRuleRecord, PricingVersion
from app.services.pricing import getPricingService

ALLOWED_MODES = {"fixed", "token", "metered"}


def _serializeRuleData(rule: dict[str, Any]) -> dict[str, Any]:
    """把数据库写入字段转换为管理端公开的通用 Token 单价字段。"""
    return {
        "featureCode": rule["featureCode"],
        "displayName": rule["displayName"],
        "billingMode": rule["billingMode"],
        "unitName": rule["unitName"],
        "unitSize": int(rule["unitSize"]),
        "fixedCost": int(rule["fixedCost"]),
        "baseCost": int(rule["baseCost"]),
        "perUnitCost": int(rule["perUnitCost"]),
        "inputTokenCostPerUnit": int(rule["inputTokenCostPer1K"]),
        "outputTokenCostPerUnit": int(rule["outputTokenCostPer1K"]),
        "tokenPricingVersion": 2 if rule["billingMode"] == "token" else 1,
        "minCost": int(rule["minCost"]),
        "maxCost": int(rule["maxCost"]),
        "enabled": bool(rule["enabled"]),
    }


def _serializeRule(rule: PricingRuleRecord) -> dict[str, Any]:
    ruleMeta = dict(rule.ruleMeta or {})
    usesAffordableTokenPricing = rule.billingMode == "token" and int(ruleMeta.get("tokenPricingVersion", 0) or 0) >= 2
    return {
        "featureCode": rule.featureCode,
        "displayName": rule.displayName,
        "billingMode": rule.billingMode,
        "unitName": rule.unitName,
        "unitSize": (int(rule.unitSize or 1) if usesAffordableTokenPricing or rule.billingMode != "token" else 1_000),
        "fixedCost": int(rule.fixedCost or 0),
        "baseCost": int(rule.baseCost or 0),
        "perUnitCost": int(rule.perUnitCost or 0),
        "inputTokenCostPerUnit": int(rule.inputTokenCostPer1K or 0),
        "outputTokenCostPerUnit": int(rule.outputTokenCostPer1K or 0),
        "tokenPricingVersion": 2 if usesAffordableTokenPricing else 1,
        "minCost": int(rule.minCost or 0),
        "maxCost": int(rule.maxCost or 0),
        "enabled": bool(rule.enabled),
    }


def _validateRule(raw: dict[str, Any]) -> dict[str, Any]:
    featureCode = str(raw.get("featureCode", "")).strip()
    displayName = str(raw.get("displayName", "")).strip()
    billingMode = str(raw.get("billingMode", "")).strip()
    unitName = str(raw.get("unitName", "")).strip()
    if not featureCode or len(featureCode) > 64 or not featureCode.replace("_", "").isalnum():
        raise ApiError("PRICING_RULE_INVALID", "功能编码只能包含字母、数字和下划线")
    if not displayName or len(displayName) > 80:
        raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的显示名称无效")
    if billingMode not in ALLOWED_MODES:
        raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的计费模式无效")
    if not unitName or len(unitName) > 32:
        raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的计量单位无效")

    normalizedRaw = dict(raw)
    if "inputTokenCostPerUnit" not in normalizedRaw:
        normalizedRaw["inputTokenCostPerUnit"] = normalizedRaw.get("inputTokenCostPer1K", 0)
    if "outputTokenCostPerUnit" not in normalizedRaw:
        normalizedRaw["outputTokenCostPerUnit"] = normalizedRaw.get("outputTokenCostPer1K", 0)
    numbers: dict[str, int] = {}
    for field in (
        "fixedCost",
        "baseCost",
        "perUnitCost",
        "inputTokenCostPerUnit",
        "outputTokenCostPerUnit",
        "minCost",
        "maxCost",
    ):
        try:
            value = int(normalizedRaw.get(field, 0) or 0)
        except (TypeError, ValueError) as error:
            raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的 {field} 不是整数") from error
        if value < 0 or value > 1_000_000:
            raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的 {field} 超出允许范围")
        numbers[field] = value
    try:
        unitSize = int(raw.get("unitSize", 1) or 1)
    except (TypeError, ValueError) as error:
        raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的计量数量不是整数") from error
    if unitSize < 1 or unitSize > 1_000_000:
        raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的计量数量超出允许范围")
    if numbers["minCost"] > numbers["maxCost"]:
        raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 的最低费用不能高于最高费用")
    if billingMode == "fixed":
        numbers["minCost"] = numbers["fixedCost"]
        numbers["maxCost"] = numbers["fixedCost"]
    elif billingMode == "token" and not (numbers["inputTokenCostPerUnit"] or numbers["outputTokenCostPerUnit"]):
        raise ApiError("PRICING_RULE_INVALID", f"{featureCode} 至少要配置一种 Token 单价")

    return {
        "featureCode": featureCode,
        "displayName": displayName,
        "billingMode": billingMode,
        "unitName": unitName,
        "unitSize": unitSize,
        "fixedCost": numbers["fixedCost"],
        "baseCost": numbers["baseCost"],
        "perUnitCost": numbers["perUnitCost"],
        # 数据库列名为历史兼容名称；新价格的真实计量单位由 unitSize 决定。
        "inputTokenCostPer1K": numbers["inputTokenCostPerUnit"],
        "outputTokenCostPer1K": numbers["outputTokenCostPerUnit"],
        "minCost": numbers["minCost"],
        "maxCost": numbers["maxCost"],
        "ruleMeta": {"tokenPricingVersion": 2} if billingMode == "token" else None,
        "enabled": bool(raw.get("enabled", True)),
    }


def getPricingOverview() -> dict[str, Any]:
    with getDb() as db:
        versions = (
            db.execute(select(PricingVersion).order_by(PricingVersion.versionId.desc()).limit(20)).scalars().all()
        )
        active = next((item for item in versions if item.status == "published"), None)
        if active is None:
            catalog = getPricingService().publicCatalog(db)
            rules = list(catalog["rules"])
            activeVersion = catalog["version"]
        else:
            records = (
                db.execute(
                    select(PricingRuleRecord)
                    .where(PricingRuleRecord.versionId == active.versionId)
                    .order_by(PricingRuleRecord.ruleId.asc())
                )
                .scalars()
                .all()
            )
            rules = [_serializeRule(record) for record in records]
            activeVersion = active.versionCode
        return {
            "activeVersion": activeVersion,
            "rules": rules,
            "versions": [
                {
                    "versionCode": version.versionCode,
                    "status": version.status,
                    "note": version.note,
                    "createdBy": version.createdBy,
                    "publishedBy": version.publishedBy,
                    "publishedAt": version.publishedAt,
                    "createdAt": version.createdAt,
                }
                for version in versions
            ],
        }


def createPricingDraft(actor: str, rawRules: list[dict[str, Any]], note: str = "") -> dict[str, Any]:
    if not rawRules:
        raise ApiError("PRICING_RULE_INVALID", "价格目录不能为空")
    rules = [_validateRule(raw) for raw in rawRules]
    featureCodes = [rule["featureCode"] for rule in rules]
    if len(featureCodes) != len(set(featureCodes)):
        raise ApiError("PRICING_RULE_INVALID", "功能编码不能重复")
    versionCode = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
    with getDb() as db:
        version = PricingVersion(
            versionCode=versionCode,
            status="draft",
            note=str(note or "")[:255],
            createdBy=actor,
        )
        db.add(version)
        db.flush()
        for rule in rules:
            db.add(PricingRuleRecord(versionId=version.versionId, **rule))
        db.commit()
    return {
        "versionCode": versionCode,
        "status": "draft",
        "rules": [_serializeRuleData(rule) for rule in rules],
    }


def publishPricingVersion(versionCode: str, actor: str) -> dict[str, Any]:
    now = datetime.now(UTC).replace(tzinfo=None)
    with getDb() as db:
        target = db.execute(
            select(PricingVersion).where(PricingVersion.versionCode == versionCode).with_for_update()
        ).scalar_one_or_none()
        if target is None:
            raise ApiError("PRICING_VERSION_NOT_FOUND")
        if target.status == "published":
            return {"versionCode": target.versionCode, "status": target.status, "publishedAt": target.publishedAt}
        if target.status != "draft":
            raise ApiError("CONFLICT", "只有草稿价格版本可以发布")
        ruleCount = len(
            db.execute(select(PricingRuleRecord.ruleId).where(PricingRuleRecord.versionId == target.versionId))
            .scalars()
            .all()
        )
        if ruleCount == 0:
            raise ApiError("PRICING_RULE_INVALID", "不能发布空价格目录")
        published = (
            db.execute(select(PricingVersion).where(PricingVersion.status == "published").with_for_update())
            .scalars()
            .all()
        )
        for version in published:
            version.status = "retired"
        target.status = "published"
        target.publishedBy = actor
        target.publishedAt = now
        db.commit()
        return {"versionCode": target.versionCode, "status": target.status, "publishedAt": target.publishedAt}


__all__ = ["createPricingDraft", "getPricingOverview", "publishPricingVersion"]
