"""Monitoring results and the alerts they raised.

monitoring_metrics: one row per (metric, asset, model version, as-of time). Re-running a check
for the same as-of time updates the row (idempotent). `details` keeps the evidence (bin
proportions, KS p-value, sample sizes, reference window) so a breach can be investigated.

monitoring_alerts: every breach that was notified, suppressed (cooldown) or failed to deliver.
It's the audit trail AND the deduplication memory: "was this alert key delivered in the last
N hours?" is answered from here, not from a cache that can be flushed.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class MonitoringMetric(Base):
    __tablename__ = "monitoring_metrics"
    __table_args__ = (
        UniqueConstraint(
            "metric_name", "asset_id", "model_version_id", "ts", postgresql_nulls_not_distinct=True
        ),
        Index("ix_monitoring_metrics_name_ts", "metric_name", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE")
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float | None] = mapped_column(Float)  # None = not computable (e.g. too few)
    threshold: Mapped[float | None] = mapped_column(Float)
    breached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MonitoringAlert(Base):
    __tablename__ = "monitoring_alerts"
    __table_args__ = (
        CheckConstraint("severity IN ('warning', 'critical')", name="severity_valid"),
        CheckConstraint(
            "status IN ('delivered', 'suppressed', 'failed', 'logged')", name="status_valid"
        ),
        Index("ix_monitoring_alerts_key_created", "alert_key", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    alert_key: Mapped[str] = mapped_column(String(160), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE")
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float | None] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    delivery_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
