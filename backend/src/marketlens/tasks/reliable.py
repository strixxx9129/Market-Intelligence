"""Reliability wrapper shared by every scheduled task.

For each ATTEMPT of a task:
1. a `pipeline_runs` row  (pipeline="task:<job>", params, attempt, celery ids, status, error)
2. a Redis lock           one run of the same job at a time; a second one is recorded as
                          `skipped` (locked) and stops its chain (Celery `Ignore`)
3. the work               an async function calling an existing service
4. the outcome            success / skipped / failed, or `retrying` when a TRANSIENT error will
                          be retried: autoretry with exponential backoff + jitter, max 5 retries

Writes inside the work are idempotent (UPSERTs on natural keys), so a retry, a duplicate
delivery (acks_late) or a manual re-run can never duplicate data.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from celery import Task
from celery.exceptions import Ignore
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.config import get_settings
from marketlens.core.errors import TRANSIENT_EXCEPTIONS
from marketlens.core.locks import RedisLock
from marketlens.core.logging import get_logger
from marketlens.models import PipelineRunStatus
from marketlens.repositories import ingestion_repo
from marketlens.tasks import runtime

logger = get_logger(__name__)

RETRY_POLICY: dict[str, Any] = {
    "autoretry_for": TRANSIENT_EXCEPTIONS,
    "retry_backoff": True,  # 1s, 2s, 4s, 8s, ... between attempts
    "retry_backoff_max": 600,  # never wait more than 10 minutes
    "retry_jitter": True,  # randomise each delay so retries don't stampede in sync
    "max_retries": 5,
}


@dataclass
class RunContext:
    run_id: uuid.UUID
    sessionmaker: async_sessionmaker[AsyncSession]
    params: dict[str, Any]
    attempt: int


Work = Callable[[RunContext], Awaitable[dict[str, Any]]]


async def _start(job: str, params: dict[str, Any], task: Task) -> uuid.UUID:
    async with runtime.task_sessionmaker() as sm, sm() as s, s.begin():
        run_id = await ingestion_repo.create_run(s, f"task:{job}", params)
        await ingestion_repo.update_run_meta(
            s,
            run_id,
            attempt=task.request.retries + 1,
            celery_task_id=task.request.id,
            root_id=task.request.root_id or task.request.id,
        )
        return run_id


async def _finish(
    run_id: uuid.UUID, status: PipelineRunStatus, result: dict[str, Any] | None, error: str | None
) -> None:
    async with runtime.task_sessionmaker() as sm, sm() as s, s.begin():
        await ingestion_repo.finish_run(
            s,
            run_id,
            status,
            rows_processed=(result or {}).get("rows"),
            error=error,
            extra_params={"result": result} if result else None,
        )


async def _work(work: Work, ctx_args: dict[str, Any]) -> dict[str, Any]:
    async with runtime.task_sessionmaker() as sm:
        return await work(RunContext(sessionmaker=sm, **ctx_args))


def run_reliably(
    task: Task,
    job: str,
    params: dict[str, Any],
    work: Work,
    lock_ttl: int,
    lock_scope: str | None = None,
) -> dict[str, Any]:
    attempt = task.request.retries + 1
    run_id = asyncio.run(_start(job, params, task))
    log = logger.bind(job=job, run_id=str(run_id), attempt=attempt)

    key = f"{get_settings().lock_prefix}:{job}:{lock_scope or 'global'}"
    lock = RedisLock(runtime.redis_client(), key, lock_ttl)
    if not lock.acquire():
        asyncio.run(_finish(run_id, PipelineRunStatus.SKIPPED, None, f"locked: {key} is held"))
        log.warning("task_skipped_locked", lock=key)
        raise Ignore()  # stop here and stop the rest of the chain

    try:
        result = asyncio.run(_work(work, {"run_id": run_id, "params": params, "attempt": attempt}))
    except TRANSIENT_EXCEPTIONS as exc:
        will_retry = task.request.retries < (task.max_retries or 0)
        status = PipelineRunStatus.RETRYING if will_retry else PipelineRunStatus.FAILED
        asyncio.run(_finish(run_id, status, None, f"{type(exc).__name__}: {exc}"))
        log.warning("task_transient_error", error=repr(exc), will_retry=will_retry)
        raise  # Celery's autoretry schedules the next attempt with backoff + jitter
    except Ignore:
        raise
    except Exception as exc:
        asyncio.run(_finish(run_id, PipelineRunStatus.FAILED, None, f"{type(exc).__name__}: {exc}"))
        log.error("task_failed", error=repr(exc))
        raise
    finally:
        lock.release()

    status = (
        PipelineRunStatus.SKIPPED
        if result.get("status") == "skipped"
        else PipelineRunStatus.SUCCESS
    )
    asyncio.run(_finish(run_id, status, result, None))
    log.info("task_finished", status=status.value)
    return {**result, "pipeline_run_id": str(run_id)}
