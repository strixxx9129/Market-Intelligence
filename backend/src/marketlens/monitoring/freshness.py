"""Data freshness: hours since the newest daily candle CLOSED.

A candle with ts = 2026-10-18 (open time) is complete at 2026-10-19 00:00 UTC. Measuring from
the close (ts + 24 h) means "0 hours" = perfectly up to date, and the threshold reads as "how
long may the source lag before we worry?". Stale data is the most common silent failure: the
pipeline keeps running, forecasts keep being served, but they're about yesterday's world.
"""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.config import get_settings
from marketlens.monitoring.types import MetricResult
from marketlens.repositories import monitoring_repo


async def check_freshness(session: AsyncSession, now: datetime) -> list[MetricResult]:
    limit = get_settings().freshness_max_hours
    as_of = now.replace(minute=0, second=0, microsecond=0)
    results = []
    for row in await monitoring_repo.latest_candles(session):
        if row.latest_ts is None:
            results.append(
                MetricResult(
                    "freshness_hours",
                    as_of,
                    None,
                    limit,
                    True,
                    asset_id=row.id,
                    severity="critical",
                    message=f"{row.symbol}: no candles at all",
                    details={"latest_candle_ts": None},
                )
            )
            continue
        closed_at = row.latest_ts + timedelta(days=1)
        hours = round((now - closed_at).total_seconds() / 3600, 2)
        breached = hours > limit
        results.append(
            MetricResult(
                "freshness_hours",
                as_of,
                hours,
                limit,
                breached,
                asset_id=row.id,
                severity="critical",
                message=(
                    f"{row.symbol}: newest daily candle closed {hours:.0f} h ago "
                    f"(limit {limit:.0f} h). Ingestion may have stopped."
                ),
                details={
                    "latest_candle_ts": row.latest_ts.isoformat(),
                    "closed_at": closed_at.isoformat(),
                },
            )
        )
    return results
