"""SQL for backtests and the inputs they read (model versions, OOS predictions)."""

import uuid
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import Backtest, ModelVersion, OosPrediction, TrainingRun


async def get_model_version(session: AsyncSession, model_version_id: int) -> ModelVersion | None:
    return await session.get(ModelVersion, model_version_id)


async def find_version(
    session: AsyncSession, training_run_id: uuid.UUID, algo: str
) -> ModelVersion | None:
    return await session.scalar(
        select(ModelVersion).where(
            ModelVersion.training_run_id == training_run_id, ModelVersion.algo == algo
        )
    )


async def get_training_run(session: AsyncSession, run_id: uuid.UUID) -> TrainingRun | None:
    return await session.get(TrainingRun, run_id)


async def load_predictions(session: AsyncSession, model_version_id: int) -> pd.DataFrame:
    rows = (
        await session.execute(
            select(OosPrediction.ts, OosPrediction.fold, OosPrediction.y_pred, OosPrediction.proba)
            .where(OosPrediction.model_version_id == model_version_id)
            .order_by(OosPrediction.ts)
        )
    ).all()
    frame = pd.DataFrame(rows, columns=["ts", "fold", "y_pred", "proba"])
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("ts"), utc=True), name="ts")
    return frame


async def create_backtest(session: AsyncSession, **values: Any) -> Backtest:
    bt = Backtest(**values)
    session.add(bt)
    await session.flush()
    return bt


async def list_model_versions(session: AsyncSession, limit: int = 50) -> list[ModelVersion]:
    return list(
        (
            await session.scalars(
                select(ModelVersion).order_by(ModelVersion.id.desc()).limit(limit)
            )
        ).all()
    )


async def latest_backtests(session: AsyncSession) -> list[Backtest]:
    """Most recent backtest per model version."""
    rows = (await session.scalars(select(Backtest).order_by(Backtest.created_at.desc()))).all()
    seen: set[int] = set()
    latest = []
    for bt in rows:
        if bt.model_version_id not in seen:
            seen.add(bt.model_version_id)
            latest.append(bt)
    return latest


async def missing_equity_curves(
    session: AsyncSession, include_existing: bool = False
) -> list[Backtest]:
    q = select(Backtest).where(Backtest.equity_curve_uri.is_not(None))
    if not include_existing:
        q = q.where(Backtest.equity_curve.is_(None))
    return list((await session.scalars(q)).all())
