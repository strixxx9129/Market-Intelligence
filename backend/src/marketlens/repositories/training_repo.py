"""SQL for training_runs, model_versions and oos_predictions."""

import uuid
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import ModelVersion, OosPrediction, PipelineRunStatus, TrainingRun

CHUNK_SIZE = 5_000


async def create_training_run(session: AsyncSession, **values: Any) -> uuid.UUID:
    run = TrainingRun(status=PipelineRunStatus.RUNNING, **values)
    session.add(run)
    await session.flush()
    return run.id


async def finish_training_run(
    session: AsyncSession, run_id: uuid.UUID, status: PipelineRunStatus, **values: Any
) -> None:
    await session.execute(
        update(TrainingRun)
        .where(TrainingRun.id == run_id)
        .values(status=status, finished_at=func.now(), **values)
    )


async def create_model_version(
    session: AsyncSession,
    *,
    name: str,
    algo: str,
    task: str,
    params: dict[str, Any],
    feature_set_id: int,
    training_run_id: uuid.UUID,
    train_start: datetime,
    train_end: datetime,
    metrics: dict[str, Any],
    mlflow_run_id: str | None,
    artifact_uri: str | None,
) -> ModelVersion:
    """New immutable version = previous max version for this name + 1."""
    current = await session.scalar(
        select(func.max(ModelVersion.version)).where(ModelVersion.name == name)
    )
    mv = ModelVersion(
        name=name,
        version=(current or 0) + 1,
        algo=algo,
        task=task,
        params=params,
        feature_set_id=feature_set_id,
        training_run_id=training_run_id,
        train_start=train_start,
        train_end=train_end,
        metrics=metrics,
        mlflow_run_id=mlflow_run_id,
        artifact_uri=artifact_uri,
        stage="candidate",
    )
    session.add(mv)
    await session.flush()
    return mv


async def insert_oos_predictions(
    session: AsyncSession, run_id: uuid.UUID, model_version_id: int, oos: pd.DataFrame
) -> int:
    rows = [
        {
            "training_run_id": run_id,
            "model_version_id": model_version_id,
            "fold": int(r.fold),
            "ts": ts.to_pydatetime(),
            "y_true": float(r.y_true),
            "y_pred": float(r.y_pred),
            "proba": None if pd.isna(r.proba) else float(r.proba),
        }
        for ts, r in zip(oos.index, oos.itertuples(index=False), strict=True)
    ]
    for i in range(0, len(rows), CHUNK_SIZE):
        await session.execute(insert(OosPrediction), rows[i : i + CHUNK_SIZE])
    return len(rows)


async def latest_successful_run(session: AsyncSession, task: str) -> TrainingRun | None:
    return await session.scalar(
        select(TrainingRun)
        .where(TrainingRun.task == task, TrainingRun.status == PipelineRunStatus.SUCCESS)
        .order_by(TrainingRun.finished_at.desc())
        .limit(1)
    )
