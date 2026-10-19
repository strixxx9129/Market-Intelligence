"""SQL for monitoring: inputs (candles, features, predictions, outcomes) and results."""

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import (
    Asset,
    Feature,
    MarketPrice,
    ModelVersion,
    MonitoringAlert,
    MonitoringMetric,
    OosPrediction,
    Prediction,
    PredictionOutcome,
)


async def latest_candles(session: AsyncSession) -> list[Any]:
    q = (
        select(Asset.id, Asset.symbol, func.max(MarketPrice.ts).label("latest_ts"))
        .outerjoin(MarketPrice, (MarketPrice.asset_id == Asset.id) & (MarketPrice.interval == "1d"))
        .where(Asset.is_active.is_(True))
        .group_by(Asset.id, Asset.symbol)
    )
    return list((await session.execute(q)).all())


async def production_models(session: AsyncSession) -> list[ModelVersion]:
    q = select(ModelVersion).where(
        ModelVersion.stage == "production", ModelVersion.task == "classification"
    )
    return list((await session.scalars(q)).all())


async def asset_id_by_symbol(session: AsyncSession, symbol: str) -> int | None:
    return await session.scalar(select(Asset.id).where(Asset.symbol == symbol.upper()))


async def feature_rows(
    session: AsyncSession, asset_id: int, feature_set_id: int, start: datetime, end: datetime
) -> list[Any]:
    q = (
        select(Feature.ts, Feature.values)
        .where(
            Feature.asset_id == asset_id,
            Feature.feature_set_id == feature_set_id,
            Feature.ts >= start,
            Feature.ts <= end,
        )
        .order_by(Feature.ts)
    )
    return list((await session.execute(q)).all())


async def latest_feature_ts(
    session: AsyncSession, asset_id: int, feature_set_id: int, until: datetime
) -> datetime | None:
    return await session.scalar(
        select(func.max(Feature.ts)).where(
            Feature.asset_id == asset_id,
            Feature.feature_set_id == feature_set_id,
            Feature.ts <= until,
        )
    )


async def live_p_up(
    session: AsyncSession, model_version_id: int, start: datetime, end: datetime
) -> list[float]:
    q = select(Prediction.p_up).where(
        Prediction.model_version_id == model_version_id,
        Prediction.source == "live",
        Prediction.as_of_ts >= start,
        Prediction.as_of_ts <= end,
        Prediction.p_up.is_not(None),
    )
    return [float(x) for x in (await session.scalars(q)).all() if x is not None]


async def oos_p_up(session: AsyncSession, model_version_id: int) -> list[float]:
    q = select(OosPrediction.proba).where(
        OosPrediction.model_version_id == model_version_id, OosPrediction.proba.is_not(None)
    )
    return [float(x) for x in (await session.scalars(q)).all() if x is not None]


async def scored_live(
    session: AsyncSession, asset_id: int, start: datetime, end: datetime
) -> list[Any]:
    q = (
        select(
            Prediction.p_up,
            Prediction.y_pred,
            PredictionOutcome.realized_up,
            PredictionOutcome.log_loss,
            PredictionOutcome.brier,
            PredictionOutcome.correct,
        )
        .join(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(
            Prediction.asset_id == asset_id,
            Prediction.source == "live",
            Prediction.target_ts >= start,
            Prediction.target_ts <= end,
        )
    )
    return list((await session.execute(q)).all())


async def upsert_metrics(session: AsyncSession, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    base = insert(MonitoringMetric)
    stmt = base.on_conflict_do_update(
        constraint="uq_monitoring_metrics_metric_name_asset_id_model_version_id_ts",
        set_={
            k: base.excluded[k]
            for k in ("value", "threshold", "breached", "details", "pipeline_run_id")
        }
        | {"computed_at": func.now()},
    )
    await session.execute(stmt, rows)


async def last_notified(
    session: AsyncSession, alert_key: str, since: datetime
) -> MonitoringAlert | None:
    """Most recent alert for this key that actually reached someone (delivered or logged)."""
    return await session.scalar(
        select(MonitoringAlert)
        .where(
            MonitoringAlert.alert_key == alert_key,
            MonitoringAlert.created_at >= since,
            MonitoringAlert.status.in_(("delivered", "logged")),
        )
        .order_by(MonitoringAlert.created_at.desc())
        .limit(1)
    )


async def add_alert(session: AsyncSession, **values: Any) -> MonitoringAlert:
    alert = MonitoringAlert(**values)
    session.add(alert)
    await session.flush()
    return alert


async def recent_alerts(session: AsyncSession, since: datetime) -> list[MonitoringAlert]:
    q = (
        select(MonitoringAlert)
        .where(MonitoringAlert.created_at >= since, MonitoringAlert.status != "suppressed")
        .order_by(MonitoringAlert.created_at.desc())
    )
    return list((await session.scalars(q)).all())
