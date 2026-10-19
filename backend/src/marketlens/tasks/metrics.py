"""Prometheus metrics for Celery tasks, via Celery signals.

Celery's prefork pool runs tasks in CHILD processes, but Prometheus scrapes one HTTP endpoint
per worker container. prometheus-client's multiprocess mode solves this: every child writes its
samples to files in PROMETHEUS_MULTIPROC_DIR, and the worker's main process serves the
aggregate on :9808 (reachable only inside the compose network).
"""

import os
import shutil
import time
from pathlib import Path
from typing import Any

from celery import signals
from prometheus_client import CollectorRegistry, Counter, Histogram, multiprocess, start_http_server

from marketlens.core.logging import get_logger

logger = get_logger(__name__)
METRICS_PORT = 9808

TASK_DURATION = Histogram(
    "celery_task_duration_seconds",
    "Celery task run time",
    ["task"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800),
)
TASKS = Counter("celery_tasks_total", "Celery task outcomes", ["task", "state"])
RETRIES = Counter("celery_task_retries_total", "Celery task retries", ["task"])

_started: dict[str, float] = {}


@signals.worker_init.connect  # type: ignore[untyped-decorator]
def _serve_metrics(**_: Any) -> None:
    directory = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if not directory:
        return  # not a monitored worker (e.g. tests, one-off CLI runs)
    path = Path(directory)
    shutil.rmtree(path, ignore_errors=True)  # stale samples from a previous container run
    path.mkdir(parents=True, exist_ok=True)
    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
    start_http_server(METRICS_PORT, registry=registry)
    logger.info("celery_metrics_server", port=METRICS_PORT)


@signals.worker_process_shutdown.connect  # type: ignore[untyped-decorator]
def _child_exit(pid: int | None = None, **_: Any) -> None:
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(pid or os.getpid())  # type: ignore[no-untyped-call]


@signals.task_prerun.connect  # type: ignore[untyped-decorator]
def _task_start(task_id: str | None = None, **_: Any) -> None:
    if task_id:
        _started[task_id] = time.perf_counter()


@signals.task_postrun.connect  # type: ignore[untyped-decorator]
def _task_end(
    task_id: str | None = None, task: Any = None, state: str | None = None, **_: Any
) -> None:
    name = getattr(task, "name", "unknown")
    started = _started.pop(task_id or "", None)
    if started is not None:
        TASK_DURATION.labels(name).observe(time.perf_counter() - started)
    TASKS.labels(name, state or "UNKNOWN").inc()


@signals.task_retry.connect  # type: ignore[untyped-decorator]
def _task_retry(sender: Any = None, **_: Any) -> None:
    RETRIES.labels(getattr(sender, "name", "unknown")).inc()
