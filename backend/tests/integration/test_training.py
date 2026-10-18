from pathlib import Path

import mlflow
import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.db.seed import seed_assets
from marketlens.features.builder import FeatureBuilder
from marketlens.ml.report import render_report
from marketlens.ml.splits import WalkForwardSplit
from marketlens.ml.train import TrainConfig, Trainer
from marketlens.models import (
    FeatureSet,
    ModelVersion,
    OosPrediction,
    PipelineRunStatus,
    TrainingRun,
)
from marketlens.repositories import ingestion_repo, price_repo

Sessionmaker = async_sessionmaker[AsyncSession]
N_DAYS = 600
SPLIT = WalkForwardSplit(initial_train=200, test_size=60, gap=2)


@pytest.fixture(autouse=True)
def _allow_mlflow_file_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests log to a throwaway local file store (no server). MLflow 3 requires an explicit
    opt-in for that backend; production uses the tracking server instead."""
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")


@pytest.fixture
async def features_built(db_sessionmaker: Sessionmaker) -> Sessionmaker:
    rng = np.random.default_rng(11)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, N_DAYS)))
    vol = rng.lognormal(6, 1, N_DAYS)
    days = pd.date_range("2020-01-01", periods=N_DAYS, freq="1D", tz="UTC")
    async with db_sessionmaker() as s, s.begin():
        await seed_assets(s)
        asset_id = await ingestion_repo.get_asset_id(s, "BTC-USD")
        assert asset_id is not None
        await price_repo.upsert_prices(
            s,
            [
                {
                    "asset_id": asset_id,
                    "interval": "1d",
                    "ts": d.to_pydatetime(),
                    "open": price_repo.to_decimal(c),
                    "high": price_repo.to_decimal(c * 1.03),
                    "low": price_repo.to_decimal(c * 0.97),
                    "close": price_repo.to_decimal(c),
                    "volume": price_repo.to_decimal(v),
                    "source": "test",
                }
                for d, c, v in zip(days, close, vol, strict=True)
            ],
        )
    await FeatureBuilder(db_sessionmaker).build("v1")
    return db_sessionmaker


def _cfg(task: str, **kw: object) -> TrainConfig:
    kw.setdefault("importance", False)  # slow; exercised in the main end-to-end test only
    return TrainConfig(task=task, split=SPLIT, start=None, importance_repeats=2, **kw)  # type: ignore[arg-type]


def _trainer(sm: Sessionmaker, tmp_path: Path) -> Trainer:
    return Trainer(sm, tracking_uri=(tmp_path / "mlruns").as_uri(), git_sha="abc1234")


@pytest.mark.parametrize("task", ["classification", "regression"])
async def test_trains_all_models_on_identical_folds(
    features_built: Sessionmaker, tmp_path: Path, task: str
) -> None:
    result = await _trainer(features_built, tmp_path).train(_cfg(task, importance=True))

    m = result.metrics
    assert len(m["models"]) == 5 and set(result.model_versions.values()) == {1}
    n_folds = len(m["folds"])
    assert n_folds == result.n_folds >= 4
    # Same folds for everyone: every model has one metric value per fold.
    for entry in m["models"].values():
        assert all(len(v) == n_folds for v in entry["per_fold"].values())
    baselines = [n for n, e in m["models"].items() if e["is_baseline"]]
    assert len(baselines) == 2
    assert all(m["models"][n]["vs_best_baseline"] for n in m["models"] if n not in baselines)
    assert m["verdict"]["best_model"] not in baselines

    async with features_built() as s:
        run = await s.get(TrainingRun, result.run_id)
        n_oos = await s.scalar(select(func.count()).select_from(OosPrediction))
        versions = (await s.scalars(select(ModelVersion))).all()
    assert run is not None and run.status is PipelineRunStatus.SUCCESS
    assert run.git_sha == "abc1234" and run.seed == 42 and run.dataset_hash
    assert run.split_config["gap"] == 2 and run.mlflow_run_id == result.mlflow_run_id
    assert n_oos == 5 * sum(f["n_test"] for f in m["folds"])
    assert all(v.stage == "candidate" and v.artifact_uri for v in versions)

    # MLflow got a parent run plus one nested run per model, with per-fold metrics.
    mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
    runs = mlflow.search_runs(experiment_names=[f"marketlens-btc-usd-{task}"])
    assert len(runs) == 6
    child = runs[runs["tags.mlflow.runName"] == m["verdict"]["best_model"]].iloc[0]
    assert child["tags.git_sha"] == "abc1234" and child["tags.feature_set"] == "v1"
    history = mlflow.MlflowClient().get_metric_history(
        child["run_id"], f"fold_{m['primary_metric']}"
    )
    assert len(history) == n_folds


async def test_oos_predictions_are_strictly_after_their_training_window(
    features_built: Sessionmaker, tmp_path: Path
) -> None:
    result = await _trainer(features_built, tmp_path).train(
        _cfg("classification", models=["logistic"])
    )
    folds = {f["index"]: f for f in result.metrics["folds"]}
    async with features_built() as s:
        preds = (await s.scalars(select(OosPrediction))).all()
    for p in preds:
        fold = folds[p.fold]
        assert pd.Timestamp(p.ts) >= pd.Timestamp(fold["test_start"])
        assert pd.Timestamp(p.ts) > pd.Timestamp(fold["train_end"]) + pd.Timedelta(days=SPLIT.gap)
        assert 0.0 <= (p.proba or 0.0) <= 1.0


async def test_model_versions_are_immutable_except_stage(
    features_built: Sessionmaker, tmp_path: Path
) -> None:
    await _trainer(features_built, tmp_path).train(_cfg("classification", models=["logistic"]))
    async with features_built() as s, s.begin():
        await s.execute(update(ModelVersion).values(stage="staging"))  # allowed
    with pytest.raises(DBAPIError, match="immutable"):
        async with features_built() as s, s.begin():
            await s.execute(update(ModelVersion).values(params={"C": 999}))  # rejected by trigger


async def test_retraining_creates_new_versions(
    features_built: Sessionmaker, tmp_path: Path
) -> None:
    trainer = _trainer(features_built, tmp_path)
    await trainer.train(_cfg("classification", models=["historical_up_rate", "logistic"]))
    second = await trainer.train(_cfg("classification", models=["historical_up_rate", "logistic"]))
    assert second.model_versions == {"historical_up_rate": 2, "logistic": 2}


async def test_report_renders_from_stored_runs(
    features_built: Sessionmaker, tmp_path: Path
) -> None:
    await _trainer(features_built, tmp_path).train(_cfg("classification"))
    async with features_built() as s:
        run = (await s.scalars(select(TrainingRun))).one()
        fs = await s.get(FeatureSet, run.feature_set_id)
    assert fs is not None
    md = render_report([(run, fs)])
    assert "### Verdict" in md and "### Calibration" in md and "historical_up_rate" in md
    assert "not financial advice" in md


async def test_failed_training_is_recorded(features_built: Sessionmaker, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not classification models"):
        await _trainer(features_built, tmp_path).train(_cfg("classification", models=["ridge"]))
    tiny = WalkForwardSplit(initial_train=10_000)
    with pytest.raises(ValueError, match="too few"):
        await _trainer(features_built, tmp_path).train(
            TrainConfig(task="classification", split=tiny, start=None)
        )


async def test_missing_feature_set_explains_fix(
    db_sessionmaker: Sessionmaker, tmp_path: Path
) -> None:
    async with db_sessionmaker() as s, s.begin():
        await seed_assets(s)
    with pytest.raises(LookupError, match="build-features"):
        await _trainer(db_sessionmaker, tmp_path).train(_cfg("classification"))
