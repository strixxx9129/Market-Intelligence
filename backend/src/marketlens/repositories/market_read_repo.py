"""Read-only SQL for the public API (rule 5: the API only reads precomputed data)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, cast, func, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import (
    Asset,
    Backtest,
    Feature,
    FeatureSet,
    MarketPrice,
    ModelVersion,
    PipelineRun,
    Prediction,
    PredictionOutcome,
    PromotionDecision,
    TrainingRun,
)


async def list_assets(session: AsyncSession) -> list[Any]:
    """Each active asset with its two newest daily candles (LATERAL ... LIMIT 2 uses the
    UNIQUE(asset_id, interval, ts) index). One row per candle; assets without data get one
    row with NULL ts/close."""
    last_two = (
        select(MarketPrice.ts, cast(MarketPrice.close, Float).label("close"))
        .where(MarketPrice.asset_id == Asset.id, MarketPrice.interval == "1d")
        .order_by(MarketPrice.ts.desc())
        .limit(2)
        .lateral()
    )
    q = (
        select(Asset, last_two.c.ts, last_two.c.close)
        .outerjoin(last_two, true())
        .where(Asset.is_active.is_(True))
        .order_by(Asset.symbol, last_two.c.ts.desc())
    )
    return list((await session.execute(q)).all())


async def get_asset(session: AsyncSession, symbol: str) -> Asset | None:
    return await session.scalar(select(Asset).where(Asset.symbol == symbol.upper()))


async def price_page(
    session: AsyncSession,
    asset_id: int,
    interval: str,
    after: datetime | None,
    start: datetime | None,
    end: datetime | None,
    limit: int,
) -> list[Any]:
    """Keyset ("cursor") pagination: WHERE ts > last_seen ORDER BY ts LIMIT n+1.
    Uses the UNIQUE(asset_id, interval, ts) index, so every page costs the same."""
    q = select(
        MarketPrice.ts,
        cast(MarketPrice.open, Float).label("open"),
        cast(MarketPrice.high, Float).label("high"),
        cast(MarketPrice.low, Float).label("low"),
        cast(MarketPrice.close, Float).label("close"),
        cast(MarketPrice.volume, Float).label("volume"),
    ).where(MarketPrice.asset_id == asset_id, MarketPrice.interval == interval)
    if after is not None:
        q = q.where(MarketPrice.ts > after)
    if start is not None:
        q = q.where(MarketPrice.ts >= start)
    if end is not None:
        q = q.where(MarketPrice.ts <= end)
    return list((await session.execute(q.order_by(MarketPrice.ts).limit(limit + 1))).all())


async def latest_features(
    session: AsyncSession, asset_id: int, feature_set_id: int, limit: int
) -> list[Any]:
    q = (
        select(Feature.ts, Feature.values)
        .where(Feature.asset_id == asset_id, Feature.feature_set_id == feature_set_id)
        .order_by(Feature.ts.desc())
        .limit(limit)
    )
    return list(reversed((await session.execute(q)).all()))


async def get_feature_set(session: AsyncSession, feature_set_id: int) -> FeatureSet | None:
    return await session.get(FeatureSet, feature_set_id)


async def feature_set_by_version(session: AsyncSession, version: str) -> FeatureSet | None:
    return await session.scalar(select(FeatureSet).where(FeatureSet.version == version))


async def latest_live_prediction(
    session: AsyncSession, asset_id: int, model_version_id: int
) -> Prediction | None:
    return await session.scalar(
        select(Prediction)
        .where(
            Prediction.asset_id == asset_id,
            Prediction.model_version_id == model_version_id,
            Prediction.source == "live",
        )
        .order_by(Prediction.as_of_ts.desc())
        .limit(1)
    )


async def scored_live_predictions(
    session: AsyncSession, asset_id: int, since: datetime
) -> list[Any]:
    """Live forecasts (any model that was in production at the time) with known outcomes."""
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
            Prediction.target_ts >= since,
        )
    )
    return list((await session.execute(q)).all())


async def latest_training_runs(session: AsyncSession) -> list[TrainingRun]:
    from marketlens.models import PipelineRunStatus

    runs = []
    for task in ("classification", "regression"):
        run = await session.scalar(
            select(TrainingRun)
            .where(TrainingRun.task == task, TrainingRun.status == PipelineRunStatus.SUCCESS)
            .order_by(TrainingRun.finished_at.desc())
            .limit(1)
        )
        if run is not None:
            runs.append(run)
    return runs


async def versions_of_run(session: AsyncSession, run_id: uuid.UUID) -> list[ModelVersion]:
    return list(
        (
            await session.scalars(
                select(ModelVersion).where(ModelVersion.training_run_id == run_id)
            )
        ).all()
    )


async def get_model_version(session: AsyncSession, mv_id: int) -> ModelVersion | None:
    return await session.get(ModelVersion, mv_id)


async def get_training_run(session: AsyncSession, run_id: uuid.UUID) -> TrainingRun | None:
    return await session.get(TrainingRun, run_id)


async def decisions_for(session: AsyncSession, mv_id: int) -> list[PromotionDecision]:
    q = (
        select(PromotionDecision)
        .where(
            (PromotionDecision.candidate_model_version_id == mv_id)
            | (PromotionDecision.production_model_version_id == mv_id)
            | (PromotionDecision.baseline_model_version_id == mv_id)
        )
        .order_by(PromotionDecision.decided_at.desc())
    )
    return list((await session.scalars(q)).all())


async def backtests_for(session: AsyncSession, mv_id: int) -> list[Backtest]:
    q = (
        select(Backtest)
        .where(Backtest.model_version_id == mv_id)
        .order_by(Backtest.created_at.desc())
    )
    return list((await session.scalars(q)).all())


async def list_backtests(session: AsyncSession, limit: int) -> list[Backtest]:
    q = select(Backtest).order_by(Backtest.created_at.desc()).limit(limit)
    return list((await session.scalars(q)).all())


async def get_backtest(session: AsyncSession, backtest_id: uuid.UUID) -> Backtest | None:
    return await session.get(Backtest, backtest_id)


async def last_task_runs(session: AsyncSession) -> list[PipelineRun]:
    """Most recent attempt of each scheduled job."""
    ranked = (
        select(
            PipelineRun.id,
            func.row_number()
            .over(partition_by=PipelineRun.pipeline, order_by=PipelineRun.started_at.desc())
            .label("rn"),
        )
        .where(PipelineRun.pipeline.startswith("task:"))
        .subquery()
    )
    q = (
        select(PipelineRun)
        .join(ranked, ranked.c.id == PipelineRun.id)
        .where(ranked.c.rn == 1)
        .order_by(PipelineRun.pipeline)
    )
    return list((await session.scalars(q)).all())


async def latest_feature_ts(session: AsyncSession, asset_id: int) -> datetime | None:
    return await session.scalar(select(func.max(Feature.ts)).where(Feature.asset_id == asset_id))


async def latest_forecast_as_of(session: AsyncSession, asset_id: int) -> datetime | None:
    return await session.scalar(
        select(func.max(Prediction.as_of_ts)).where(
            Prediction.asset_id == asset_id, Prediction.source == "live"
        )
    )
