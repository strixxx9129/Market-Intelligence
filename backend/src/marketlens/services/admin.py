"""Operator actions: replay a failed task run, launch backfills and chains by hand.

retry: a failed (or still-retrying) task run is replayed with its ORIGINAL parameters, as
recorded in pipeline_runs.params. Safe because every task is idempotent: replaying a run
that partially wrote data only re-UPSERTs the same rows.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.models import PipelineRunStatus
from marketlens.repositories import ingestion_repo

RETRYABLE = (PipelineRunStatus.FAILED, PipelineRunStatus.RETRYING)
RESULT_KEYS = ("result", "skip_reason")  # added to params when a run finishes, not inputs


@dataclass
class RetryPlan:
    run_id: uuid.UUID
    task_name: str
    kwargs: dict[str, Any]


async def plan_retry(
    sessionmaker: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> RetryPlan:
    async with sessionmaker() as s:
        run = await ingestion_repo.get_run(s, run_id)
    if run is None:
        raise LookupError(f"pipeline run {run_id} not found")
    if not run.pipeline.startswith("task:"):
        raise ValueError(
            f"run {run_id} is a service-level run ({run.pipeline}); retry its task run "
            f"(parent {run.parent_run_id}) instead"
        )
    if run.status not in RETRYABLE:
        raise ValueError(f"run {run_id} is {run.status.value}; only failed runs can be retried")
    job = run.pipeline.removeprefix("task:")
    kwargs = {k: v for k, v in run.params.items() if k not in RESULT_KEYS}
    if job == "ingest":
        kwargs.pop("refresh", None)  # derived from settings at run time, not an argument
    elif job == "train_candidates":
        kwargs.pop("task", None)
    return RetryPlan(run_id=run.id, task_name=f"marketlens.{job}", kwargs=kwargs)
