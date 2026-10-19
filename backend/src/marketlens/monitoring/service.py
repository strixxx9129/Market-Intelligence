"""Entry points used by Celery tasks (and tests): run a check, record it, alert on breaches."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.cache import invalidate_namespaces
from marketlens.monitoring.alerts import Notifier, default_notifier, record_and_alert
from marketlens.monitoring.drift import check_drift
from marketlens.monitoring.freshness import check_freshness
from marketlens.monitoring.performance import check_performance
from marketlens.monitoring.types import MetricResult

Check = Callable[[AsyncSession, datetime], Awaitable[list[MetricResult]]]
CHECKS: dict[str, Check] = {
    "freshness": check_freshness,
    "drift": check_drift,
    "performance": check_performance,
}


async def run_check(
    name: str,
    sessionmaker: async_sessionmaker[AsyncSession],
    now: datetime,
    notifier: Notifier | None = None,
    pipeline_run_id: uuid.UUID | None = None,
    use_default_notifier: bool = True,
) -> dict[str, Any]:
    async with sessionmaker() as s:
        results = await CHECKS[name](s, now)
    if notifier is None and use_default_notifier:
        notifier = default_notifier()
    counts = await record_and_alert(sessionmaker, results, now, notifier, pipeline_run_id)
    if counts["breached"]:
        await invalidate_namespaces("status")  # /status lists recent monitoring alerts
    return {
        "status": "success",
        "rows": counts["metrics"],
        **counts,
        "breaches": sorted({r.metric_name for r in results if r.breached}),
    }
