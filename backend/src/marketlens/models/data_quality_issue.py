"""Quarantine / audit table for data-quality problems found during ingestion.

- severity=error   → the row was NOT loaded (quarantined); `details` keeps the offending values.
- severity=warning → the row WAS loaded, but someone should know (zero volume, extreme move, ...).

Natural key UNIQUE(asset_id, interval, ts, check_name) with NULLS NOT DISTINCT: re-running the
same ingestion updates existing issues instead of piling up duplicates. `ts` is NULL only for
problems without a usable timestamp (e.g. a CSV line with a garbled timestamp).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class IssueSeverity(enum.StrEnum):
    WARNING = "warning"
    ERROR = "error"


class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    __table_args__ = (
        UniqueConstraint(
            "asset_id", "interval", "ts", "check_name", postgresql_nulls_not_distinct=True
        ),
        Index("ix_data_quality_issues_asset_id_ts", "asset_id", "ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    check_name: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[IssueSeverity] = mapped_column(
        Enum(IssueSeverity, name="issue_severity", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
