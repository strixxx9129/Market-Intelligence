"""Celery tasks. Thin: resolve parameters, then call existing services via `run_reliably`
(pipeline_runs row per attempt, Redis lock, retry on transient errors).

Arguments are JSON-friendly (dates as ISO strings) and fully resolved before they're
recorded, so `marketlens admin retry <run>` can replay a failed run with exactly the same
parameters.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import Any

from celery import Task, chain

from marketlens.core.cache import invalidate_namespaces
from marketlens.core.config import get_settings
from marketlens.core.errors import PermanentError
from marketlens.features.builder import FeatureBuilder
from marketlens.ingestion.connectors.base import get_connector
from marketlens.ingestion.pipeline import IngestionPipeline, IngestMode
from marketlens.ml.dataset import load_dataset
from marketlens.ml.train import TrainConfig, Trainer
from marketlens.repositories import ingestion_repo, prediction_repo
from marketlens.services.data_health import check_health
from marketlens.services.prediction import PredictionService
from marketlens.services.promotion import PromotionService
from marketlens.tasks.celery_app import celery_app
from marketlens.tasks.reliable import RETRY_POLICY, RunContext, run_reliably

MINUTE, HOUR = 60, 3600


def _task(name: str) -> Callable[[Callable[..., dict[str, Any]]], Any]:
    """Register a bound task with the shared retry policy (celery itself is untyped)."""
    decorator: Callable[[Callable[..., dict[str, Any]]], Any] = celery_app.task(
        name=f"marketlens.{name}", bind=True, **RETRY_POLICY
    )
    return decorator


def _asset(asset: str | None) -> str:
    return asset or get_settings().asset_symbol


# ---------------------------------------------------------------- daily chain steps
@_task("ingest")
def ingest(
    self: Task, asset: str | None = None, source: str = "kaggle_btc", mode: str = "incremental"
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "asset": _asset(asset),
        "source": source,
        "mode": mode,
        "refresh": get_settings().kaggle_refresh,
    }

    async def work(ctx: RunContext) -> dict[str, Any]:
        connector = get_connector(params["source"])
        refreshed = await asyncio.to_thread(connector.refresh) if params["refresh"] else None
        result = await IngestionPipeline(ctx.sessionmaker, connector, params["asset"]).run(
            IngestMode(params["mode"]), parent_run_id=ctx.run_id
        )
        if result.rows_changed:
            await invalidate_namespaces("assets", "prices", "status")
        return {
            # An unchanged file is "skipped" by the pipeline; the chain still continues.
            "status": "skipped" if result.status.value == "skipped" else "success",
            "rows": result.rows_changed,
            "refresh": refreshed,
            "ingest_run_id": str(result.run_id),
            "skip_reason": result.skip_reason,
        }

    return run_reliably(self, "ingest", params, work, lock_ttl=30 * MINUTE)


@_task("validate")
def validate(self: Task, asset: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"asset": _asset(asset)}

    async def work(ctx: RunContext) -> dict[str, Any]:
        async with ctx.sessionmaker() as s:
            report = await check_health(s, params["asset"])
        if not report.ok:
            # Permanent: retrying won't fix bad data. Failing here stops the chain, so no
            # features or forecasts are built on top of it.
            raise PermanentError("data health check failed: " + "; ".join(report.failures))
        return {"status": "success", **report.as_dict()}

    return run_reliably(self, "validate", params, work, lock_ttl=10 * MINUTE)


@_task("build_features")
def build_features(
    self: Task, asset: str | None = None, feature_set: str | None = None
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "asset": _asset(asset),
        "feature_set": feature_set or get_settings().feature_set,
    }

    async def work(ctx: RunContext) -> dict[str, Any]:
        r = await FeatureBuilder(ctx.sessionmaker).build(
            params["feature_set"], params["asset"], parent_run_id=ctx.run_id
        )
        if r.rows_changed:
            await invalidate_namespaces("features", "status")
        return {
            "status": "success",
            "rows": r.rows_changed,
            "feature_rows": r.rows,
            "last_ts": r.last_ts.isoformat() if r.last_ts is not None else None,
        }

    return run_reliably(self, "build_features", params, work, lock_ttl=30 * MINUTE)


@_task("predict")
def predict(self: Task, asset: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"asset": _asset(asset)}

    async def work(ctx: RunContext) -> dict[str, Any]:
        return await PredictionService(ctx.sessionmaker).predict(
            params["asset"], pipeline_run_id=ctx.run_id
        )

    return run_reliably(self, "predict", params, work, lock_ttl=15 * MINUTE)


# ---------------------------------------------------------------- other scheduled jobs
@_task("evaluate_outcomes")
def evaluate_outcomes(self: Task) -> dict[str, Any]:
    async def work(ctx: RunContext) -> dict[str, Any]:
        return await PredictionService(ctx.sessionmaker).evaluate_outcomes()

    return run_reliably(self, "evaluate_outcomes", {}, work, lock_ttl=15 * MINUTE)


@_task("train_candidates")
def train_candidates(
    self: Task, asset: str | None = None, feature_set: str | None = None
) -> dict[str, Any]:
    settings = get_settings()
    params: dict[str, Any] = {
        "asset": _asset(asset),
        "feature_set": feature_set or settings.feature_set,
        "task": "classification",
    }

    async def work(ctx: RunContext) -> dict[str, Any]:
        async with ctx.sessionmaker() as s:
            data = await load_dataset(s, params["feature_set"], "classification", params["asset"])
            asset_id = await ingestion_repo.get_asset_id(s, params["asset"])
            assert asset_id is not None
            latest = await prediction_repo.latest_training_run(s, "classification", asset_id)
        if (
            latest is not None
            and latest.dataset_hash == data.dataset_hash
            and latest.git_sha == settings.git_sha
        ):
            # Same data, same code, same seed → identical results. Don't burn 10 minutes.
            return {
                "status": "skipped",
                "reason": "no new data or code since last training",
                "training_run_id": str(latest.id),
            }
        result = await Trainer(ctx.sessionmaker).train(
            TrainConfig(
                feature_set=params["feature_set"], task="classification", asset=params["asset"]
            )
        )
        staged = await PromotionService(ctx.sessionmaker).mark_staging(result.run_id)
        return {
            "status": "success",
            "training_run_id": str(result.run_id),
            "staging": staged.name if staged else None,
            "verdict": result.metrics["verdict"],
        }

    return run_reliably(self, "train_candidates", params, work, lock_ttl=4 * HOUR)


@_task("promote_model")
def promote_model(self: Task, asset: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"asset": _asset(asset)}

    async def work(ctx: RunContext) -> dict[str, Any]:
        decision = await PromotionService(ctx.sessionmaker).promote(params["asset"], ctx.run_id)
        return {"status": "success", **decision}

    return run_reliably(self, "promote_model", params, work, lock_ttl=10 * MINUTE)


@_task("backfill")
def backfill(self: Task, start: str, end: str, asset: str | None = None) -> dict[str, Any]:
    """Rebuild features and write predictions for start..end with source='backfill'.

    Backfilled forecasts come from TODAY's production model, which may have been trained on
    those very days, so they are NOT out-of-sample and are excluded from live evaluation.
    """
    params: dict[str, Any] = {"start": start, "end": end, "asset": _asset(asset)}

    async def work(ctx: RunContext) -> dict[str, Any]:
        await FeatureBuilder(ctx.sessionmaker).build(
            get_settings().feature_set, params["asset"], parent_run_id=ctx.run_id
        )
        return await PredictionService(ctx.sessionmaker).predict(
            params["asset"],
            start=datetime.fromisoformat(params["start"]),
            end=datetime.fromisoformat(params["end"]),
            source="backfill",
            pipeline_run_id=ctx.run_id,
        )

    return run_reliably(self, "backfill", params, work, lock_ttl=HOUR)


# ---------------------------------------------------------------- chains (launched by Beat)
def daily_chain(asset: str | None = None) -> Any:
    """ingest → validate → build_features → predict.

    `.si()` = immutable signature: each step takes its own arguments and ignores the previous
    step's return value. A step that fails (after retries) or is skipped by a lock stops the
    chain, so nothing downstream runs on missing or bad data.
    """
    return chain(
        ingest.si(asset=asset),
        validate.si(asset=asset),
        build_features.si(asset=asset),
        predict.si(asset=asset),
    )


def weekly_chain(asset: str | None = None) -> Any:
    return chain(train_candidates.si(asset=asset), promote_model.si(asset=asset))


@celery_app.task(name="marketlens.daily_cycle")  # type: ignore[untyped-decorator]
def daily_cycle(asset: str | None = None) -> str:
    return str(daily_chain(asset).apply_async().id)


@celery_app.task(name="marketlens.weekly_training")  # type: ignore[untyped-decorator]
def weekly_training(asset: str | None = None) -> str:
    return str(weekly_chain(asset).apply_async().id)
