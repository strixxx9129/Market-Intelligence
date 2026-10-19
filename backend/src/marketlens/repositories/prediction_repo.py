"""SQL for predictions, prediction_outcomes, promotion_decisions and model stages."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, and_, cast, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from marketlens.models import (
    MarketPrice,
    ModelVersion,
    Prediction,
    PredictionOutcome,
    PromotionDecision,
    TrainingRun,
)


def model_prefix(asset: str, interval: str = "1d") -> str:
    return f"{asset.lower()}_{interval}_"


# ---------------------------------------------------------------- model stages
async def production_model(
    session: AsyncSession, asset: str, task: str = "classification"
) -> ModelVersion | None:
    return await session.scalar(
        select(ModelVersion).where(
            ModelVersion.stage == "production",
            ModelVersion.task == task,
            ModelVersion.name.startswith(model_prefix(asset)),
        )
    )


async def models_in_stage(
    session: AsyncSession, asset: str, stage: str, task: str = "classification"
) -> list[ModelVersion]:
    return list(
        (
            await session.scalars(
                select(ModelVersion)
                .where(
                    ModelVersion.stage == stage,
                    ModelVersion.task == task,
                    ModelVersion.name.startswith(model_prefix(asset)),
                )
                .order_by(ModelVersion.created_at.desc(), ModelVersion.id.desc())
            )
        ).all()
    )


async def set_stage(session: AsyncSession, model_version_id: int, stage: str) -> None:
    # The immutability trigger allows exactly this one column to change.
    await session.execute(
        update(ModelVersion).where(ModelVersion.id == model_version_id).values(stage=stage)
    )


async def version_by_algo(
    session: AsyncSession, training_run_id: uuid.UUID, algo: str
) -> ModelVersion | None:
    return await session.scalar(
        select(ModelVersion).where(
            ModelVersion.training_run_id == training_run_id, ModelVersion.algo == algo
        )
    )


async def latest_training_run(
    session: AsyncSession, task: str, asset_id: int
) -> TrainingRun | None:
    from marketlens.models import PipelineRunStatus

    return await session.scalar(
        select(TrainingRun)
        .where(
            TrainingRun.task == task,
            TrainingRun.asset_id == asset_id,
            TrainingRun.status == PipelineRunStatus.SUCCESS,
        )
        .order_by(TrainingRun.finished_at.desc())
        .limit(1)
    )


async def record_decision(session: AsyncSession, **values: Any) -> PromotionDecision:
    decision = PromotionDecision(**values)
    session.add(decision)
    await session.flush()
    return decision


# ---------------------------------------------------------------- predictions
async def upsert_predictions(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Idempotent on (model_version_id, asset_id, as_of_ts, horizon).

    A re-run updates the same row instead of inserting a duplicate. A backfill never
    overwrites a live forecast (update only when the source matches).
    """
    if not rows:
        return 0
    base = insert(Prediction)
    stmt = base.on_conflict_do_update(
        constraint="uq_predictions_model_version_id_asset_id_as_of_ts_horizon",
        set_={
            "y_pred": base.excluded.y_pred,
            "p_up": base.excluded.p_up,
            "target_ts": base.excluded.target_ts,
            "pipeline_run_id": base.excluded.pipeline_run_id,
        },
        where=Prediction.source == base.excluded.source,
    )
    await session.execute(stmt, rows)
    return len(rows)


async def count_predictions(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Prediction)) or 0)


async def pending_outcomes(session: AsyncSession, now: datetime) -> list[Any]:
    """Predictions without an outcome whose target day is complete AND has a candle, joined
    with the as_of close and the target close (both from market_prices)."""
    asof, target = aliased(MarketPrice), aliased(MarketPrice)
    q = (
        select(
            Prediction.id,
            Prediction.p_up,
            Prediction.y_pred,
            cast(asof.close, Float).label("close_asof"),
            cast(target.close, Float).label("close_target"),
        )
        .join(
            asof,
            and_(
                asof.asset_id == Prediction.asset_id,
                asof.ts == Prediction.as_of_ts,
                asof.interval == "1d",
            ),
        )
        .join(
            target,
            and_(
                target.asset_id == Prediction.asset_id,
                target.ts == Prediction.target_ts,
                target.interval == "1d",
            ),
        )
        .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(
            PredictionOutcome.id.is_(None),
            Prediction.target_ts + literal_column("interval '1 day'") <= now,
        )
    )
    return list((await session.execute(q)).all())


async def upsert_outcomes(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    base = insert(PredictionOutcome)
    stmt = base.on_conflict_do_update(
        index_elements=[PredictionOutcome.prediction_id],
        set_={
            k: base.excluded[k]
            for k in ("realized_return", "realized_up", "log_loss", "brier", "correct")
        },
    )
    await session.execute(stmt, rows)
    return len(rows)
