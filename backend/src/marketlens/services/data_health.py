"""`validate` step of the daily chain: is the freshly ingested data safe to build on?

Hard failures (stop the chain, nothing downstream runs on bad data):
- no candles at all
- missing days inside the last N days (a gap would silently corrupt rolling features)
- error-level daily issues recorded by the most recent ingestion (quarantined candles)

Warnings (recorded, chain continues):
- the newest candle is older than `health_max_staleness_days` (the source hasn't updated yet;
  downstream steps are idempotent, so re-running on the same data is harmless)
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.config import get_settings
from marketlens.models import DataQualityIssue, IssueSeverity, MarketPrice, PipelineRun
from marketlens.repositories import ingestion_repo


@dataclass
class HealthReport:
    ok: bool
    latest_ts: datetime | None
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "latest_ts": self.latest_ts.isoformat() if self.latest_ts else None,
            "failures": self.failures,
            "warnings": self.warnings,
        }


async def check_health(
    session: AsyncSession, asset: str, interval: str = "1d", now: datetime | None = None
) -> HealthReport:
    settings = get_settings()
    now = now or datetime.now(UTC)
    asset_id = await ingestion_repo.get_asset_id(session, asset)
    if asset_id is None:
        return HealthReport(False, None, failures=[f"asset {asset} not found"])

    latest = await session.scalar(
        select(func.max(MarketPrice.ts)).where(
            MarketPrice.asset_id == asset_id, MarketPrice.interval == interval
        )
    )
    if latest is None:
        return HealthReport(False, None, failures=["no candles stored"])
    report = HealthReport(True, latest)

    window_start = latest - timedelta(days=settings.health_gap_lookback_days)
    days = (
        await session.scalars(
            select(MarketPrice.ts).where(
                MarketPrice.asset_id == asset_id,
                MarketPrice.interval == interval,
                MarketPrice.ts >= window_start,
            )
        )
    ).all()
    expected = pd.date_range(window_start, latest, freq="1D")
    missing = expected.difference(pd.DatetimeIndex(days))
    if len(missing):
        report.failures.append(
            f"{len(missing)} missing day(s) in the last {settings.health_gap_lookback_days} days "
            f"(first: {missing[0].date()})"
        )

    last_ingest = await session.scalar(
        select(PipelineRun.id)
        .where(PipelineRun.pipeline == "ingest")
        .order_by(PipelineRun.started_at.desc())
        .limit(1)
    )
    if last_ingest is not None:
        errors = await session.scalar(
            select(func.count())
            .select_from(DataQualityIssue)
            .where(
                DataQualityIssue.pipeline_run_id == last_ingest,
                DataQualityIssue.severity == IssueSeverity.ERROR,
                DataQualityIssue.interval == interval,
            )
        )
        if errors:
            report.failures.append(f"{errors} quarantined candle(s) in the latest ingestion")

    stale_days = (now - latest).days
    if stale_days > settings.health_max_staleness_days:
        report.warnings.append(f"newest candle is {stale_days} days old ({latest.date()})")
    report.ok = not report.failures
    return report
