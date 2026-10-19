"""Platform status: data freshness, the last run of each job, and derived system alerts.

System alerts are computed on read from pipeline state; they are not user alerts.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.config import get_settings
from marketlens.repositories import market_read_repo as repo
from marketlens.repositories import prediction_repo
from marketlens.schemas.status import Freshness, JobRun, StatusOut, SystemAlert


async def platform_status(session: AsyncSession, now: datetime | None = None) -> StatusOut:
    now = now or datetime.now(UTC)
    settings = get_settings()
    alerts: list[SystemAlert] = []

    freshness = []
    for row in await repo.list_assets(session):
        asset = row.Asset
        latest = row.latest_ts
        stale = (now - latest).days if latest else None
        freshness.append(
            Freshness(
                symbol=asset.symbol,
                latest_candle_ts=latest,
                latest_feature_ts=await repo.latest_feature_ts(session, asset.id),
                latest_forecast_as_of=await repo.latest_forecast_as_of(session, asset.id),
                stale_days=stale,
            )
        )
        if latest is None:
            alerts.append(
                SystemAlert(
                    severity="critical",
                    code="no_data",
                    message=f"{asset.symbol}: no candles stored",
                )
            )
        elif stale is not None and stale > settings.health_max_staleness_days:
            alerts.append(
                SystemAlert(
                    severity="warning",
                    code="stale_data",
                    message=f"{asset.symbol}: newest candle is {stale} days old",
                )
            )

    runs = await repo.last_task_runs(session)
    for r in runs:
        recent = now - r.started_at < timedelta(hours=48)
        if recent and r.status.value == "failed":
            alerts.append(
                SystemAlert(
                    severity="critical",
                    code="job_failed",
                    message=f"{r.pipeline}: last run failed ({(r.error or '')[:120]})",
                )
            )
        elif recent and r.status.value == "retrying":
            alerts.append(
                SystemAlert(
                    severity="warning",
                    code="job_retrying",
                    message=f"{r.pipeline}: retrying after a transient error",
                )
            )

    production = await prediction_repo.production_model(session, settings.asset_symbol)
    production_name = f"{production.name} v{production.version}" if production else None
    if production is None:
        alerts.append(
            SystemAlert(
                severity="warning", code="no_production_model", message="no model in production"
            )
        )
    else:
        run = await repo.get_training_run(session, production.training_run_id)
        if run and run.metrics.get("models", {}).get(production.algo, {}).get("is_baseline"):
            alerts.append(
                SystemAlert(
                    severity="info",
                    code="baseline_in_production",
                    message="production serves a baseline: no model has passed the promotion gate",
                )
            )

    degraded = any(a.severity != "info" for a in alerts)
    return StatusOut(
        status="degraded" if degraded else "ok",
        generated_at=now,
        freshness=freshness,
        last_runs=[
            JobRun(
                job=r.pipeline.removeprefix("task:"),
                status=r.status.value,
                attempt=r.attempt,
                started_at=r.started_at,
                finished_at=r.finished_at,
                error=r.error,
            )
            for r in runs
        ],
        production_model=production_name,
        alerts=alerts,
    )
