"""Audit log of every pipeline/job execution (ingestion, features, training, ...).

`input_hash` lets an ingestion job detect that an identical raw file was already processed
successfully and skip it (idempotency).
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class PipelineRunStatus(enum.StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"  # nothing to do, e.g. this exact raw file was already ingested
    RETRYING = "retrying"  # this attempt failed transiently; another attempt is scheduled


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
    # Celery reliability bookkeeping (Phase 7): one row per task ATTEMPT.
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    celery_task_id: Mapped[str | None] = mapped_column(String(64), index=True)
    root_id: Mapped[str | None] = mapped_column(String(64), index=True)  # the chain it belongs to
    # Service-level runs (e.g. "ingest") point at the task attempt that triggered them.
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL")
    )
