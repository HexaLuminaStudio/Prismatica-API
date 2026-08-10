"""版本化定价模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

BIGINT_ID = BigInteger().with_variant(Integer, "sqlite")


class PricingVersion(Base):
    """一次可发布、可追溯的完整价格目录。"""

    __tablename__ = "pricing_versions"

    versionId: Mapped[int] = mapped_column("version_id", BIGINT_ID, primary_key=True, autoincrement=True)
    versionCode: Mapped[str] = mapped_column("version_code", String(40), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    note: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    createdBy: Mapped[str] = mapped_column("created_by", String(64), nullable=False)
    publishedBy: Mapped[str | None] = mapped_column("published_by", String(64), nullable=True)
    publishedAt: Mapped[datetime | None] = mapped_column("published_at", DateTime, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (Index("idx_pricing_versions_status_time", "status", "published_at"),)


class PricingRuleRecord(Base):
    """某一价格版本内的单项规则。"""

    __tablename__ = "pricing_rules"

    ruleId: Mapped[int] = mapped_column("rule_id", BIGINT_ID, primary_key=True, autoincrement=True)
    versionId: Mapped[int] = mapped_column(
        "version_id", BIGINT_ID, ForeignKey("pricing_versions.version_id", ondelete="CASCADE"), nullable=False
    )
    featureCode: Mapped[str] = mapped_column("feature_code", String(64), nullable=False)
    displayName: Mapped[str] = mapped_column("display_name", String(80), nullable=False)
    billingMode: Mapped[str] = mapped_column("billing_mode", String(24), nullable=False)
    unitName: Mapped[str] = mapped_column("unit_name", String(32), nullable=False)
    unitSize: Mapped[int] = mapped_column("unit_size", BigInteger, nullable=False, default=1)
    fixedCost: Mapped[int] = mapped_column("fixed_cost", BigInteger, nullable=False, default=0)
    baseCost: Mapped[int] = mapped_column("base_cost", BigInteger, nullable=False, default=0)
    perUnitCost: Mapped[int] = mapped_column("per_unit_cost", BigInteger, nullable=False, default=0)
    inputTokenCostPer1K: Mapped[int] = mapped_column(
        "input_token_cost_per_1k", BigInteger, nullable=False, default=0
    )
    outputTokenCostPer1K: Mapped[int] = mapped_column(
        "output_token_cost_per_1k", BigInteger, nullable=False, default=0
    )
    minCost: Mapped[int] = mapped_column("min_cost", BigInteger, nullable=False, default=0)
    maxCost: Mapped[int] = mapped_column("max_cost", BigInteger, nullable=False, default=1000000)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    ruleMeta: Mapped[dict | None] = mapped_column("rule_meta", JSON, nullable=True)
    createdAt: Mapped[datetime] = mapped_column(
        "created_at", DateTime, nullable=False, server_default=func.current_timestamp()
    )

    __table_args__ = (
        UniqueConstraint("version_id", "feature_code", name="uk_pricing_rules_version_feature"),
        Index("idx_pricing_rules_feature", "feature_code", "version_id"),
    )


__all__ = ["PricingRuleRecord", "PricingVersion"]
