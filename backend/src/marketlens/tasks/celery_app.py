"""Celery application: broker, reliability defaults, queues and routes.

Tasks stay thin: they call the same services as the CLI (and later the API). No business
logic here or in `jobs.py`.

- JSON only: task arguments are plain data (dates as ISO strings), never pickled objects
  (pickle from a broker is a remote-code-execution risk and couples workers to code versions).
- acks_late: a task message is acknowledged AFTER it finishes, not when a worker picks it up.
  If the worker crashes mid-task, the broker re-delivers it to another worker. Requires
  idempotent tasks, which we have.
- task_reject_on_worker_lost: also re-queue when the worker process is killed (OOM, SIGKILL),
  instead of marking the task as failed.
- prefetch 1: a worker holds only the task it's running, so long jobs don't block short ones.
- Queues: separate lanes per kind of work, consumed by separate worker processes. A 20-minute
  training run can never delay the daily ingest → predict chain.
"""

from celery import Celery
from kombu import Queue

from marketlens.core.config import get_settings
from marketlens.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level, json=settings.log_json)

QUEUES = ("ingest", "features", "train", "predict")

celery_app = Celery(
    "marketlens",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["marketlens.tasks.jobs"],
)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    task_queues=[Queue(q) for q in QUEUES],
    task_default_queue="ingest",
    task_routes={
        "marketlens.ingest": {"queue": "ingest"},
        "marketlens.validate": {"queue": "ingest"},
        "marketlens.daily_cycle": {"queue": "ingest"},
        "marketlens.build_features": {"queue": "features"},
        "marketlens.backfill": {"queue": "features"},
        "marketlens.predict": {"queue": "predict"},
        "marketlens.evaluate_outcomes": {"queue": "predict"},
        "marketlens.train_candidates": {"queue": "train"},
        "marketlens.promote_model": {"queue": "train"},
        "marketlens.weekly_training": {"queue": "train"},
    },
    result_expires=7 * 24 * 3600,
    broker_transport_options={"visibility_timeout": 4 * 3600},  # > longest task (training)
)

from marketlens.tasks.schedules import BEAT_SCHEDULE  # noqa: E402

celery_app.conf.beat_schedule = BEAT_SCHEDULE


@celery_app.task(name="marketlens.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    return "pong"
