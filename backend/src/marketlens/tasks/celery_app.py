"""Celery application (worker + beat). Placeholder until Phase 2 adds real scheduled jobs.

Tasks must stay thin: they call the same services the API uses; no business logic here.
"""

from celery import Celery

from marketlens.core.config import get_settings
from marketlens.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level, json=settings.log_json)

celery_app = Celery("marketlens", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,  # re-deliver if a worker dies mid-task (tasks must be idempotent)
    worker_prefetch_multiplier=1,
    worker_hijack_root_logger=False,
    beat_schedule={},
)


@celery_app.task(name="marketlens.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    return "pong"
