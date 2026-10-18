"""Train and evaluate every model of a task on IDENTICAL walk-forward folds.

For each model and each fold:
    fresh pipeline → fit on train rows → predict the (unseen) test rows → metrics
    (+ permutation importance on the test fold for non-baselines)
Then: compare with the best baseline, metrics per era, calibration, and one final fit on ALL
rows (the deployable model). Everything is logged to MLflow (a parent run per training run, a
nested run per model) and stored in training_runs / model_versions / oos_predictions.

No hyperparameter is tuned on the test folds: every model uses fixed, conservative defaults.
Tuning on the folds you report would quietly turn them into training data.
"""

import asyncio
import math
import tempfile
import uuid
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import mlflow
import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.config import get_settings
from marketlens.core.logging import get_logger
from marketlens.ml.dataset import Dataset, load_dataset
from marketlens.ml.evaluate import (
    METRIC_DIRECTION,
    PRIMARY_METRIC,
    calibration_table,
    compare_to_baselines,
    compute_metrics,
    era_metrics,
    fold_permutation_importance,
    importance_summary,
    summarize,
)
from marketlens.ml.models import BaseModel, get_model, models_for_task
from marketlens.ml.models.base import Task
from marketlens.ml.splits import Fold, WalkForwardSplit
from marketlens.models import PipelineRunStatus
from marketlens.repositories import training_repo

logger = get_logger(__name__)

# Training runs in a worker thread (asyncio.to_thread); scikit-learn then warns that its
# thread-local config can't reach joblib workers. We set no sklearn config, so it's noise.
warnings.filterwarnings("ignore", message=r"`sklearn\.utils\.parallel\.delayed` should be used")

ERAS = {"2013-2016": ("2013", "2016"), "2017-2020": ("2017", "2020"), "2021-2026": ("2021", "2026")}


@dataclass
class TrainConfig:
    feature_set: str = "v1"
    task: Task = "classification"
    asset: str = "BTC-USD"
    interval: str = "1d"
    split: WalkForwardSplit = field(default_factory=WalkForwardSplit)
    seed: int = 42
    start: str | None = "2013-01-01"
    models: list[str] | None = None  # None = every registered model for the task
    importance: bool = True
    importance_repeats: int = 5


@dataclass
class ModelResult:
    model: BaseModel
    params: dict[str, Any]
    per_fold: pd.DataFrame  # index = fold, columns = metrics
    oos: pd.DataFrame  # index = ts; fold, y_true, y_pred, proba
    importance: dict[str, Any] | None
    final_pipeline: Any = None
    mlflow_run_id: str | None = None
    artifact_uri: str | None = None


@dataclass
class TrainResult:
    run_id: uuid.UUID
    task: Task
    primary_metric: str
    n_rows: int
    n_folds: int
    metrics: dict[str, Any]
    mlflow_run_id: str | None
    model_versions: dict[str, int]


# ---------------------------------------------------------------- pure CV
def cross_validate(
    model: BaseModel, data: Dataset, folds: list[Fold], cfg: TrainConfig
) -> ModelResult:
    params = model.params()
    X, y = data.X, data.y.to_numpy()
    fold_metrics: list[dict[str, float]] = []
    oos_parts: list[pd.DataFrame] = []
    importances: list[pd.Series] = []
    for fold in folds:
        pipe = model.build_pipeline(params, seed=cfg.seed, categorical=data.categorical)
        X_train, X_test = X.iloc[fold.train], X.iloc[fold.test]
        pipe.fit(X_train, y[fold.train])
        if data.task == "classification":
            proba = pipe.predict_proba(X_test)[:, 1]
            pred = (proba > 0.5).astype(float)
        else:
            proba, pred = None, pipe.predict(X_test)
        fold_metrics.append(compute_metrics(data.task, y[fold.test], pred, proba))
        oos_parts.append(
            pd.DataFrame(
                {
                    "fold": fold.index,
                    "y_true": y[fold.test],
                    "y_pred": pred,
                    "proba": proba if proba is not None else np.nan,
                },
                index=X_test.index,
            )
        )
        if cfg.importance and not model.is_baseline:
            importances.append(
                fold_permutation_importance(
                    pipe, X_test, y[fold.test], data.task, cfg.seed, cfg.importance_repeats
                )
            )
    per_fold = pd.DataFrame(fold_metrics)
    reference = float(per_fold[PRIMARY_METRIC[data.task]].mean())  # the model's own score
    return ModelResult(
        model=model,
        params=params,
        per_fold=per_fold,
        oos=pd.concat(oos_parts),
        importance=importance_summary(importances, reference) if importances else None,
    )


def fit_final(model: BaseModel, params: dict[str, Any], data: Dataset, seed: int) -> Any:
    """The deployable model: same recipe, trained on every row we have."""
    pipe = model.build_pipeline(params, seed=seed, categorical=data.categorical)
    return pipe.fit(data.X, data.y.to_numpy())


def fold_info(data: Dataset, folds: list[Fold]) -> list[dict[str, Any]]:
    idx = data.X.index
    return [
        {
            "index": f.index,
            "train_start": idx[f.train[0]].isoformat(),
            "train_end": idx[f.train[-1]].isoformat(),
            "test_start": idx[f.test[0]].isoformat(),
            "test_end": idx[f.test[-1]].isoformat(),
            "n_train": len(f.train),
            "n_test": len(f.test),
        }
        for f in folds
    ]


def build_metrics(data: Dataset, folds: list[Fold], results: list[ModelResult]) -> dict[str, Any]:
    task = data.task
    primary = PRIMARY_METRIC[task]
    per_fold = {r.model.name: r.per_fold for r in results}
    baselines = [r.model.name for r in results if r.model.is_baseline]
    comparisons = compare_to_baselines(per_fold, baselines) if baselines else {}

    models: dict[str, Any] = {}
    for r in results:
        entry: dict[str, Any] = {
            "family": r.model.family,
            "is_baseline": r.model.is_baseline,
            "params": r.params,
            "summary": summarize(r.per_fold),
            "per_fold": {m: r.per_fold[m].tolist() for m in r.per_fold.columns},
            "vs_best_baseline": {
                m: c.as_dict() for m, c in comparisons.get(r.model.name, {}).items()
            },
            "eras": era_metrics(task, r.oos, ERAS),
            "importance": r.importance,
            "mlflow_run_id": r.mlflow_run_id,
        }
        if task == "classification":
            entry["calibration"] = calibration_table(
                r.oos["y_true"].to_numpy(), r.oos["proba"].to_numpy()
            )
        models[r.model.name] = entry

    sign = 1 if METRIC_DIRECTION[primary] == "min" else -1
    leaderboard = sorted(models, key=lambda n: sign * models[n]["summary"][primary]["mean"])
    contenders = [n for n in leaderboard if not models[n]["is_baseline"]]
    best = contenders[0] if contenders else None
    verdict: dict[str, Any] = {"best_model": best, "beats_baseline": False}
    if best is not None and primary in models[best]["vs_best_baseline"]:
        c = models[best]["vs_best_baseline"][primary]
        verdict |= {
            "baseline": c["baseline"],
            "improvement_mean": c["improvement_mean"],
            "wins": c["wins"],
            "folds": c["folds"],
            "beats_baseline": c["significant"],
        }
    return {
        "primary_metric": primary,
        "n_rows": len(data.X),
        "date_range": [data.X.index[0].isoformat(), data.X.index[-1].isoformat()],
        "up_rate": float(data.y.gt(0).mean()),
        "folds": fold_info(data, folds),
        "leaderboard": leaderboard,
        "verdict": verdict,
        "models": models,
    }


def json_safe(value: Any) -> Any:
    """JSONB has no NaN/inf; numpy scalars aren't JSON. Normalise recursively."""
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [json_safe(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# ---------------------------------------------------------------- service
class Trainer:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        tracking_uri: str | None = None,
        git_sha: str | None = None,
    ) -> None:
        settings = get_settings()
        self.sessionmaker = sessionmaker
        self.tracking_uri = tracking_uri or settings.mlflow_tracking_uri
        self.git_sha = git_sha or settings.git_sha

    async def train(self, cfg: TrainConfig) -> TrainResult:
        models = [get_model(n) for n in cfg.models] if cfg.models else models_for_task(cfg.task)
        wrong = [m.name for m in models if m.task != cfg.task]
        if wrong:
            raise ValueError(f"models {wrong} are not {cfg.task} models")

        async with self.sessionmaker() as session:
            data = await load_dataset(
                session, cfg.feature_set, cfg.task, cfg.asset, cfg.interval, cfg.start
            )
        folds = cfg.split.folds(len(data.X))
        if not folds:
            raise ValueError(f"{len(data.X)} rows are too few for split {cfg.split.config()}")

        split_config = {**cfg.split.config(), "start": cfg.start, "n_folds": len(folds)}
        async with self.sessionmaker() as session, session.begin():
            run_id = await training_repo.create_training_run(
                session,
                task=cfg.task,
                feature_set_id=data.feature_set.id,
                asset_id=data.asset_id,
                split_config=split_config,
                dataset_hash=data.dataset_hash,
                git_sha=self.git_sha,
                seed=cfg.seed,
            )
        log = logger.bind(training_run_id=str(run_id), task=cfg.task, feature_set=cfg.feature_set)
        log.info(
            "training_started", rows=len(data.X), folds=len(folds), models=[m.name for m in models]
        )

        try:
            results, metrics, parent_id, experiment = await asyncio.to_thread(
                self._run_and_log, run_id, cfg, data, folds, models, split_config
            )
            versions = await self._store(run_id, cfg, data, results, metrics, parent_id, experiment)
        except Exception as exc:
            async with self.sessionmaker() as session, session.begin():
                await training_repo.finish_training_run(
                    session, run_id, PipelineRunStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
                )
            log.error("training_failed", error=repr(exc))
            raise
        log.info("training_finished", verdict=metrics["verdict"])
        return TrainResult(
            run_id=run_id,
            task=cfg.task,
            primary_metric=metrics["primary_metric"],
            n_rows=len(data.X),
            n_folds=len(folds),
            metrics=metrics,
            mlflow_run_id=parent_id,
            model_versions=versions,
        )

    # -- CPU + MLflow work (runs in a thread) --------------------------------
    def _run_and_log(
        self,
        run_id: uuid.UUID,
        cfg: TrainConfig,
        data: Dataset,
        folds: list[Fold],
        models: list[BaseModel],
        split_config: dict[str, Any],
    ) -> tuple[list[ModelResult], dict[str, Any], str, str]:
        if self.tracking_uri:
            mlflow.set_tracking_uri(self.tracking_uri)
        experiment = f"marketlens-{cfg.asset.lower()}-{cfg.task}"
        mlflow.set_experiment(experiment)
        tags = {
            "training_run_id": str(run_id),
            "feature_set": cfg.feature_set,
            "feature_code_hash": data.feature_set.code_hash,
            "dataset_hash": data.dataset_hash,
            "git_sha": self.git_sha,
            "seed": str(cfg.seed),
            "asset": cfg.asset,
            "task": cfg.task,
        }
        results: list[ModelResult] = []
        with mlflow.start_run(run_name=f"{cfg.feature_set}-{cfg.task}-{str(run_id)[:8]}") as parent:
            mlflow.set_tags(tags)
            mlflow.log_params({f"split.{k}": v for k, v in split_config.items()})
            mlflow.log_param("n_rows", len(data.X))
            for model in models:
                with mlflow.start_run(run_name=model.name, nested=True) as child:
                    mlflow.set_tags(
                        {
                            **tags,
                            "model": model.name,
                            "family": model.family,
                            "is_baseline": str(model.is_baseline),
                        }
                    )
                    result = cross_validate(model, data, folds, cfg)
                    mlflow.log_params({f"model.{k}": v for k, v in result.params.items()})
                    for fold_idx, row in result.per_fold.iterrows():
                        for metric, value in row.items():
                            if pd.notna(value):
                                mlflow.log_metric(
                                    f"fold_{metric}", float(value), step=int(fold_idx)
                                )
                    for metric, stats in summarize(result.per_fold).items():
                        mlflow.log_metrics(
                            {f"{metric}_mean": stats["mean"], f"{metric}_std": stats["std"]}
                        )
                    result.final_pipeline = fit_final(model, result.params, data, cfg.seed)
                    with tempfile.TemporaryDirectory() as tmp:
                        path = Path(tmp)
                        joblib.dump(result.final_pipeline, path / "model.joblib")
                        result.oos.to_csv(path / "oos_predictions.csv", index_label="ts")
                        mlflow.log_artifact(str(path / "model.joblib"))
                        mlflow.log_artifact(str(path / "oos_predictions.csv"))
                    if result.importance:
                        mlflow.log_dict(json_safe(result.importance), "permutation_importance.json")
                    result.mlflow_run_id = child.info.run_id
                    result.artifact_uri = mlflow.get_artifact_uri("model.joblib")
                results.append(result)

            metrics = build_metrics(data, folds, results)
            mlflow.log_dict(json_safe(metrics), "training_metrics.json")
            mlflow.set_tags(
                {
                    "verdict.best_model": str(metrics["verdict"]["best_model"]),
                    "verdict.beats_baseline": str(metrics["verdict"]["beats_baseline"]),
                }
            )
            return results, metrics, parent.info.run_id, experiment

    # -- database writes (one transaction) -----------------------------------
    async def _store(
        self,
        run_id: uuid.UUID,
        cfg: TrainConfig,
        data: Dataset,
        results: list[ModelResult],
        metrics: dict[str, Any],
        parent_id: str,
        experiment: str,
    ) -> dict[str, int]:
        versions: dict[str, int] = {}
        async with self.sessionmaker() as session, session.begin():
            for r in results:
                name = f"{cfg.asset.lower()}_{cfg.interval}_{r.model.name}"
                mv = await training_repo.create_model_version(
                    session,
                    name=name,
                    algo=r.model.name,
                    task=cfg.task,
                    params=json_safe(r.params),
                    feature_set_id=data.feature_set.id,
                    training_run_id=run_id,
                    train_start=data.X.index[0].to_pydatetime(),
                    train_end=data.X.index[-1].to_pydatetime(),
                    metrics=json_safe(metrics["models"][r.model.name]["summary"]),
                    mlflow_run_id=r.mlflow_run_id,
                    artifact_uri=r.artifact_uri,
                )
                versions[r.model.name] = mv.version
                metrics["models"][r.model.name]["model_version"] = {
                    "name": name,
                    "version": mv.version,
                }
                await training_repo.insert_oos_predictions(session, run_id, mv.id, r.oos)
            await training_repo.finish_training_run(
                session,
                run_id,
                PipelineRunStatus.SUCCESS,
                metrics=json_safe(metrics),
                mlflow_run_id=parent_id,
                mlflow_experiment=experiment,
            )
        return versions
