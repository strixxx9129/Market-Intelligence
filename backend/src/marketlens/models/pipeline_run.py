"""Audit log of every pipeline/job execution (ingestion, features, training, ...).

`input_hash` lets an ingestion job detect that an identical raw file was already processed
successfully and skip it (idempotency).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class PipelineRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[PipelineRunStatus] = mapped_column(
        Enum(
            PipelineRunStatus,
            name="pipeline_run_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PipelineRunStatus.RUNNING,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_processed: Mapped[int | None] = mapped_column(Integer)
    input_hash: Mapped[str | None] = mapped_column(String(64), index=True)  # sha256 hex
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
