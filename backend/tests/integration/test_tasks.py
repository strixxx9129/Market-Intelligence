"""Celery task reliability, end to end, against the real test Postgres and Redis.

These tests are SYNCHRONOUS on purpose: tasks call `asyncio.run(...)` like they do in a
worker, which can't happen inside pytest-asyncio's running loop. Most tests run Celery in
eager mode (in-process). Eager mode raises `Retry` instead of re-queueing, so `deliver()`
plays the broker's part: it re-applies the task with `retries + 1` until it finishes, which is
exactly what a worker sees on redelivery. One test uses a real in-process worker to prove the
autoretry configuration itself. Tasks commit for real, so every test starts and ends with
empty tables.
"""

import asyncio
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import joblib
import numpy as np
import pandas as pd
import pytest
from celery import chain
from celery.exceptions import Retry
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.config import get_settings
from marketlens.core.errors import PermanentError, TransientError
from marketlens.core.locks import RedisLock
from marketlens.db.base import Base
from marketlens.db.seed import seed_assets
from marketlens.features.builder import FeatureBuilder
from marketlens.ingestion.connectors import base as connector_base
from marketlens.ingestion.connectors.kaggle_btc import KaggleBtcConnector
from marketlens.models import (
    MarketPrice,
    ModelVersion,
    PipelineRun,
    PipelineRunStatus,
    Prediction,
    PredictionOutcome,
    PromotionDecision,
)
from marketlens.repositories import feature_repo, ingestion_repo, price_repo, training_repo
from marketlens.services.admin import plan_retry
from marketlens.services.promotion import PromotionService
from marketlens.tasks import jobs, runtime
from marketlens.tasks.celery_app import celery_app
from tests.fixtures.kaggle_btc_fixture import FIXTURE_PATH

Sessionmaker = async_sessionmaker[AsyncSession]


def run[T](coro_fn: Any) -> T:
    """Run an async function against the task database in a fresh event loop."""

    async def go() -> T:
        async with runtime.task_sessionmaker() as sm:
            return await coro_fn(sm)  # type: ignore[no-any-return]

    return asyncio.run(go())


@pytest.fixture
def task_env(migrated_db: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    redis_url = get_settings().redis_url.rsplit("/", 1)[0] + "/15"  # isolated Redis DB
    runtime.configure(database_url=migrated_db, redis_url=redis_url)
    monkeypatch.setattr(get_settings(), "model_cache_dir", tmp_path / "models")
    monkeypatch.setattr(get_settings(), "kaggle_refresh", False)
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))

    async def wipe(sm: Sessionmaker) -> None:
        async with sm() as s, s.begin():
            await s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    runtime.redis_client().flushdb()
    run(wipe)
    yield
    run(wipe)
    runtime.redis_client().flushdb()
    celery_app.conf.task_always_eager = False
    runtime.configure()


def deliver(task: Any, kwargs: dict[str, Any] | None = None) -> Any:
    """Apply a task eagerly, redelivering it after each `Retry` like the broker would."""
    retries = 0
    while True:
        try:
            return task.apply(kwargs=kwargs or {}, retries=retries, throw=True)
        except Retry:
            retries += 1


def _task_runs(job: str) -> list[PipelineRun]:
    async def q(sm: Sessionmaker) -> list[PipelineRun]:
        async with sm() as s:
            rows = await s.scalars(
                select(PipelineRun)
                .where(PipelineRun.pipeline == f"task:{job}")
                .order_by(PipelineRun.attempt, PipelineRun.started_at)
            )
            return list(rows.all())

    return run(q)


def _count(model: type) -> int:
    async def q(sm: Sessionmaker) -> int:
        async with sm() as s:
            return int(await s.scalar(select(func.count()).select_from(model)) or 0)

    return run(q)


def _seed(sm_days: int = 0) -> None:
    async def go(sm: Sessionmaker) -> None:
        async with sm() as s, s.begin():
            await seed_assets(s)

    run(go)


# ---------------------------------------------------------------- 1. retry on transient errors
class FlakyConnector(KaggleBtcConnector):
    """Fails twice with a transient error, then works (like a flaky network)."""

    name: ClassVar[str] = "flaky_test"
    calls: ClassVar[int] = 0

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(FIXTURE_PATH)

    def fetch(self, *args: Any, **kwargs: Any) -> Any:
        type(self).calls += 1
        if type(self).calls <= 2:
            raise TransientError(f"connection reset (call {type(self).calls})")
        return super().fetch(*args, **kwargs)


@pytest.fixture
def flaky_connector(monkeypatch: pytest.MonkeyPatch) -> type[FlakyConnector]:
    FlakyConnector.calls = 0
    monkeypatch.setitem(connector_base._REGISTRY, FlakyConnector.name, FlakyConnector)
    return FlakyConnector


def test_transient_failures_are_retried_and_every_attempt_recorded(
    task_env: None, flaky_connector: type[FlakyConnector]
) -> None:
    _seed()
    result = deliver(jobs.ingest, {"source": "flaky_test", "mode": "full"})

    assert result.successful()
    assert flaky_connector.calls == 3
    runs = _task_runs("ingest")
    assert [r.attempt for r in runs] == [1, 2, 3]
    assert [r.status.value for r in runs] == ["retrying", "retrying", "success"]
    assert all("connection reset" in (r.error or "") for r in runs[:2])
    assert runs[2].error is None
    # Same parameters on every attempt (that's what makes retries replayable).
    inputs = [{k: v for k, v in r.params.items() if k != "result"} for r in runs]
    assert inputs[0] == inputs[1] == inputs[2]
    assert inputs[0]["source"] == "flaky_test" and inputs[0]["mode"] == "full"
    assert _count(MarketPrice) == 3  # the fixture's 3 complete days, loaded once


def test_real_worker_applies_autoretry_with_backoff(
    task_env: None, flaky_connector: type[FlakyConnector]
) -> None:
    """Not eager: a real Celery worker thread consumes from an in-memory broker, so the retry
    really goes back through the broker with a backoff countdown (≤ 1 s, then ≤ 2 s)."""
    from celery.contrib.testing.worker import start_worker

    _seed()
    saved = (celery_app.conf.broker_url, celery_app.conf.result_backend)

    def reset_connections() -> None:
        # Celery caches its producer pool and result backend on first use; drop them so the
        # broker/backend switch below takes effect inside this long-lived pytest process.
        celery_app.close()
        celery_app.__dict__.pop("backend", None)
        celery_app.amqp._producer_pool = None

    celery_app.conf.task_always_eager = False
    celery_app.conf.update(broker_url="memory://", result_backend="cache+memory://")
    reset_connections()
    try:
        with start_worker(celery_app, perform_ping_check=False, queues=["ingest"]):
            started = time.monotonic()
            jobs.ingest.apply_async(kwargs={"source": "flaky_test", "mode": "full"})
            # pipeline_runs is the source of truth (Celery's result store is just a cache).
            while time.monotonic() - started < 60:
                runs = [(r.attempt, r.status.value) for r in _task_runs("ingest")]
                if runs and runs[-1][1] in ("success", "failed"):
                    break
                time.sleep(0.5)
            elapsed = time.monotonic() - started
    finally:
        celery_app.conf.update(broker_url=saved[0], result_backend=saved[1])
        reset_connections()
    assert flaky_connector.calls == 3
    assert [r.status.value for r in _task_runs("ingest")] == ["retrying", "retrying", "success"]
    assert elapsed < 30  # backoff is short at first: 1 s, 2 s (jittered), not minutes


def test_gives_up_after_max_retries(task_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    class AlwaysDown(FlakyConnector):
        name: ClassVar[str] = "always_down"

        def fetch(self, *args: Any, **kwargs: Any) -> Any:
            raise TransientError("still down")

    monkeypatch.setitem(connector_base._REGISTRY, AlwaysDown.name, AlwaysDown)
    _seed()
    with pytest.raises(TransientError):
        deliver(jobs.ingest, {"source": "always_down"})
    statuses = [r.status.value for r in _task_runs("ingest")]
    assert statuses == ["retrying"] * 5 + ["failed"]  # 1 try + 5 retries, then stop


def test_permanent_errors_fail_immediately(task_env: None) -> None:
    # No asset seeded → the pipeline raises a LookupError: a bug/config issue, not transient.
    with pytest.raises(LookupError):
        jobs.ingest.apply(kwargs={"source": "kaggle_btc"})
    runs = _task_runs("ingest")
    assert [r.status.value for r in runs] == ["failed"] and runs[0].attempt == 1


# ---------------------------------------------------------------- 2. distributed lock
def test_lock_blocks_a_concurrent_run(task_env: None) -> None:
    _seed()
    key = f"{get_settings().lock_prefix}:ingest:global"
    held = RedisLock(runtime.redis_client(), key, ttl_seconds=60)
    assert held.acquire()  # another worker is "running" ingest right now
    try:
        result = jobs.ingest.apply(kwargs={"source": "kaggle_btc"})
    finally:
        held.release()

    assert result.state == "IGNORED"  # the chain stops here
    runs = _task_runs("ingest")
    assert [r.status.value for r in runs] == ["skipped"]
    assert "locked" in (runs[0].error or "")
    assert _count(MarketPrice) == 0  # no work was done


def test_two_simultaneous_runs_only_one_executes(
    task_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Slow(FlakyConnector):
        name: ClassVar[str] = "slow_test"

        def fetch(self, *args: Any, **kwargs: Any) -> Any:
            time.sleep(2)
            return KaggleBtcConnector.fetch(self, *args, **kwargs)

    monkeypatch.setitem(connector_base._REGISTRY, Slow.name, Slow)
    _seed()
    states: list[str] = []

    def worker() -> None:
        states.append(jobs.ingest.apply(kwargs={"source": "slow_test", "mode": "full"}).state)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(states) == ["IGNORED", "SUCCESS"]
    assert sorted(r.status.value for r in _task_runs("ingest")) == ["skipped", "success"]


def test_lock_release_only_by_its_owner(task_env: None) -> None:
    client = runtime.redis_client()
    a = RedisLock(client, "marketlens:test:lock", ttl_seconds=1)
    assert a.acquire()
    time.sleep(1.2)  # a's lock expired (a crashed / was too slow)
    b = RedisLock(client, "marketlens:test:lock", ttl_seconds=30)
    assert b.acquire()
    assert a.release() is False  # a must not delete b's lock
    assert b.holder() == b.token
    assert b.release() is True


# ---------------------------------------------------------------- 3. idempotent predict
def _setup_production_model(tmp_path: Path, n_days: int = 120) -> int:
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n_days)))
    days = pd.date_range("2024-01-01", periods=n_days, freq="1D", tz="UTC")

    async def go(sm: Sessionmaker) -> int:
        async with sm() as s, s.begin():
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
                        "high": price_repo.to_decimal(c * 1.02),
                        "low": price_repo.to_decimal(c * 0.98),
                        "close": price_repo.to_decimal(c),
                        "volume": price_repo.to_decimal(100 + i),
                        "source": "test",
                    }
                    for i, (d, c) in enumerate(zip(days, close, strict=True))
                ],
            )
        built = await FeatureBuilder(sm).build("v1")
        async with sm() as s:
            fs = await feature_repo.get_feature_set(s, "v1")
            assert fs is not None
            X = await feature_repo.load_features(s, asset_id, fs.id)
        names = [f["name"] for f in fs.definition["features"]]
        model = Pipeline([("model", LogisticRegression())]).fit(
            X[names], rng.integers(0, 2, len(X))
        )
        path = tmp_path / "artifact" / "model.joblib"
        path.parent.mkdir(parents=True)
        joblib.dump(model, path)
        async with sm() as s, s.begin():
            run_id = await training_repo.create_training_run(
                s,
                task="classification",
                feature_set_id=fs.id,
                asset_id=asset_id,
                split_config={},
                git_sha="t",
                seed=1,
                metrics={},
            )
            mv = await training_repo.create_model_version(
                s,
                name="btc-usd_1d_logistic",
                algo="logistic",
                task="classification",
                params={},
                feature_set_id=fs.id,
                training_run_id=run_id,
                train_start=days[0].to_pydatetime(),
                train_end=days[-1].to_pydatetime(),
                metrics={},
                mlflow_run_id=None,
                artifact_uri=path.as_uri(),
            )
            await s.execute(
                update(ModelVersion).where(ModelVersion.id == mv.id).values(stage="production")
            )
        assert built.rows > 0
        return mv.id

    return run(go)


def test_rerunning_predict_creates_no_duplicates(task_env: None, tmp_path: Path) -> None:
    mv_id = _setup_production_model(tmp_path)

    first = jobs.predict.apply().get()
    second = jobs.predict.apply().get()

    assert first["model_version_id"] == second["model_version_id"] == mv_id
    assert first["as_of"] == second["as_of"]
    assert _count(Prediction) == 1  # same natural key → updated, not duplicated
    assert [r.status.value for r in _task_runs("predict")] == ["success", "success"]


def test_backfill_is_idempotent_and_never_overwrites_live(task_env: None, tmp_path: Path) -> None:
    _setup_production_model(tmp_path)
    live = jobs.predict.apply().get()
    as_of = datetime.fromisoformat(live["as_of"])
    start = (as_of - timedelta(days=9)).isoformat()

    for _ in range(2):
        jobs.backfill.apply(kwargs={"start": start, "end": as_of.isoformat()}).get()

    async def sources(sm: Sessionmaker) -> list[str]:
        async with sm() as s:
            return list(
                (await s.scalars(select(Prediction.source).order_by(Prediction.as_of_ts))).all()
            )

    got = run(sources)
    assert len(got) == 10  # 10 days, no duplicates after two backfills
    assert got[-1] == "live"  # the live forecast for as_of was kept, not relabelled
    assert set(got[:-1]) == {"backfill"}


def test_outcomes_are_scored_once_the_target_day_exists(task_env: None, tmp_path: Path) -> None:
    _setup_production_model(tmp_path)
    jobs.backfill.apply(
        kwargs={"start": "2024-03-01T00:00:00+00:00", "end": "2024-03-10T00:00:00+00:00"}
    ).get()
    first = jobs.evaluate_outcomes.apply().get()
    again = jobs.evaluate_outcomes.apply().get()
    assert first["rows"] == 10 and again["rows"] == 0  # already scored → nothing pending
    assert _count(PredictionOutcome) == 10


# ---------------------------------------------------------------- 4. chain stops on bad data
def test_validate_failure_stops_the_chain(task_env: None, tmp_path: Path) -> None:
    _setup_production_model(tmp_path)

    async def punch_gap(sm: Sessionmaker) -> None:
        async with sm() as s, s.begin():
            latest = await s.scalar(select(func.max(MarketPrice.ts)))
            await s.execute(
                MarketPrice.__table__.delete().where(MarketPrice.ts == latest - timedelta(days=3))
            )

    run(punch_gap)
    with pytest.raises(PermanentError, match="missing day"):
        jobs.validate.apply()

    with pytest.raises(PermanentError):
        chain(jobs.validate.si(), jobs.build_features.si(), jobs.predict.si()).apply()
    assert _task_runs("build_features") == [] and _task_runs("predict") == []
    assert all(r.status.value == "failed" for r in _task_runs("validate"))


# ---------------------------------------------------------------- 5. admin retry
def test_failed_run_can_be_retried_with_its_original_params(
    task_env: None, flaky_connector: type[FlakyConnector], monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed()
    monkeypatch.setattr(jobs.ingest, "max_retries", 0)  # first failure is final
    with pytest.raises(TransientError):
        deliver(jobs.ingest, {"source": "flaky_test", "mode": "full"})
    failed = _task_runs("ingest")[0]
    assert failed.status.value == "failed"

    plan = run(lambda sm: plan_retry(sm, failed.id))
    assert plan.task_name == "marketlens.ingest"
    assert plan.kwargs == {"asset": "BTC-USD", "source": "flaky_test", "mode": "full"}
    flaky_connector.calls = 2  # the network is back
    assert celery_app.tasks[plan.task_name].apply(kwargs=plan.kwargs).successful()
    assert _count(MarketPrice) == 3


# ---------------------------------------------------------------- 6. promotion gate
def _training_run_with_folds(cand: list[float], base: list[float]) -> tuple[Any, int, int]:
    async def go(sm: Sessionmaker) -> tuple[Any, int, int]:
        async with sm() as s, s.begin():
            await seed_assets(s)
            asset_id = await ingestion_repo.get_asset_id(s, "BTC-USD")
            fs = await feature_repo.create_feature_set(s, "v1", {"features": []}, "0" * 64)
            metrics = {
                "verdict": {"best_model": "logistic"},
                "models": {
                    "logistic": {
                        "is_baseline": False,
                        "per_fold": {"log_loss": cand},
                        "vs_best_baseline": {"log_loss": {"baseline": "historical_up_rate"}},
                        "summary": {"log_loss": {"mean": float(np.mean(cand))}},
                    },
                    "historical_up_rate": {
                        "is_baseline": True,
                        "per_fold": {"log_loss": base},
                        "vs_best_baseline": {},
                        "summary": {"log_loss": {"mean": float(np.mean(base))}},
                    },
                },
            }
            run_id = await training_repo.create_training_run(
                s,
                task="classification",
                feature_set_id=fs.id,
                asset_id=asset_id,
                split_config={},
                git_sha="t",
                seed=1,
                metrics=metrics,
            )
            await training_repo.finish_training_run(s, run_id, PipelineRunStatus.SUCCESS)
            ids = {}
            for algo in ("logistic", "historical_up_rate"):
                mv = await training_repo.create_model_version(
                    s,
                    name=f"btc-usd_1d_{algo}",
                    algo=algo,
                    task="classification",
                    params={},
                    feature_set_id=fs.id,
                    training_run_id=run_id,
                    train_start=datetime(2024, 1, 1, tzinfo=UTC),
                    train_end=datetime(2024, 6, 1, tzinfo=UTC),
                    metrics={},
                    mlflow_run_id=None,
                    artifact_uri=None,
                )
                ids[algo] = mv.id
        await PromotionService(sm).mark_staging(run_id)
        return run_id, ids["logistic"], ids["historical_up_rate"]

    return run(go)


def _stages() -> dict[str, str]:
    async def q(sm: Sessionmaker) -> dict[str, str]:
        async with sm() as s:
            return {mv.algo: mv.stage for mv in (await s.scalars(select(ModelVersion))).all()}

    return run(q)


def test_gate_rejects_and_falls_back_to_the_baseline(task_env: None) -> None:
    _training_run_with_folds(cand=[0.690] * 10 + [0.6935] * 14, base=[0.693] * 24)
    result = jobs.promote_model.apply().get()

    assert result["decision"] == "rejected"
    assert _stages() == {"logistic": "candidate", "historical_up_rate": "production"}

    async def decision(sm: Sessionmaker) -> PromotionDecision:
        async with sm() as s:
            return (await s.scalars(select(PromotionDecision))).one()

    d = run(decision)
    assert d.decision == "rejected" and d.metrics["worse_folds"] == 14
    assert any("fallback" in r for r in d.reasons)


def test_gate_promotes_a_clearly_better_model(task_env: None) -> None:
    _training_run_with_folds(cand=[0.680] * 24, base=[0.693] * 24)
    result = jobs.promote_model.apply().get()
    assert result["decision"] == "promoted"
    assert _stages() == {"logistic": "production", "historical_up_rate": "candidate"}
