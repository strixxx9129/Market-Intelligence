"""Record monitoring results, raise alerts for breaches, deduplicate, deliver.

For every breached metric:
    same alert key notified within the cooldown?  → record as `suppressed`, send nothing
    otherwise                                      → send, record `delivered` / `failed`
                                                     (`logged` when no webhook is configured)
A failed delivery doesn't count as notified, so the next run tries again. Without
deduplication, a stale feed would page someone every hour until it's fixed, and people learn
to ignore alerts that cry wolf.

Webhook payload: {"content": ..., "text": ...}. Discord reads `content`, Slack reads `text`,
and each ignores the other, so one URL setting works for both.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.config import get_settings
from marketlens.core.logging import get_logger
from marketlens.monitoring.types import MetricResult
from marketlens.repositories import monitoring_repo

logger = get_logger(__name__)
ICON = {"warning": "⚠️", "critical": "🚨"}


class Notifier(Protocol):
    async def send(self, text: str) -> None:
        """Deliver or raise."""


class WebhookNotifier:
    def __init__(self, url: str, timeout_s: float = 5.0) -> None:
        self.url = url
        self.timeout_s = timeout_s

    async def send(self, text: str) -> None:
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(self.url, json={"content": text, "text": text})
            response.raise_for_status()


def default_notifier() -> Notifier | None:
    url = get_settings().monitoring_webhook_url
    return WebhookNotifier(url) if url else None


def format_alert(r: MetricResult) -> str:
    value = "n/a" if r.value is None else f"{r.value:g}"
    return (
        f"{ICON[r.severity]} MarketLens monitoring [{r.severity}] {r.metric_name}: "
        f"{r.message} (value {value}, threshold {r.threshold:g}, as of "
        f"{r.ts:%Y-%m-%d %H:%M} UTC)"
    )


def _row(r: MetricResult, run_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "metric_name": r.metric_name,
        "asset_id": r.asset_id,
        "model_version_id": r.model_version_id,
        "ts": r.ts,
        "value": r.value,
        "threshold": r.threshold,
        "breached": r.breached,
        "details": r.details,
        "pipeline_run_id": run_id,
    }


async def record_and_alert(
    sessionmaker: async_sessionmaker[AsyncSession],
    results: list[MetricResult],
    now: datetime,
    notifier: Notifier | None = None,
    pipeline_run_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    cooldown = timedelta(hours=get_settings().monitoring_alert_cooldown_hours)
    async with sessionmaker() as s, s.begin():
        await monitoring_repo.upsert_metrics(s, [_row(r, pipeline_run_id) for r in results])

    counts = {
        "metrics": len(results),
        "breached": 0,
        "delivered": 0,
        "suppressed": 0,
        "failed": 0,
        "logged": 0,
    }
    for r in (r for r in results if r.breached):
        counts["breached"] += 1
        async with sessionmaker() as s, s.begin():
            recent = await monitoring_repo.last_notified(s, r.alert_key, now - cooldown)
            if recent is not None:
                status, error = "suppressed", None
            elif notifier is None:
                status, error = "logged", None
                logger.warning("monitoring_alert", text=format_alert(r))
            else:
                try:
                    await notifier.send(format_alert(r))
                    status, error = "delivered", None
                except Exception as exc:  # delivery must never break monitoring itself
                    status, error = "failed", f"{type(exc).__name__}: {exc}"
                    logger.error("monitoring_alert_delivery_failed", error=error)
            await monitoring_repo.add_alert(
                s,
                alert_key=r.alert_key,
                metric_name=r.metric_name,
                asset_id=r.asset_id,
                model_version_id=r.model_version_id,
                severity=r.severity,
                message=r.message,
                value=r.value,
                threshold=r.threshold,
                status=status,
                delivery_error=error,
                created_at=now,
            )
        counts[status] += 1
    return counts
