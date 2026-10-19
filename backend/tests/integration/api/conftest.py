"""API test harness: the real app (middleware, cache, limiter, auth) against the test database
and an isolated Redis DB. The API commits like in production, so tables are wiped around each
test instead of rolled back."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from marketlens.api.ratelimit import limiter
from marketlens.core.config import get_settings
from marketlens.db.base import Base
from marketlens.db.seed import seed_assets
from marketlens.features.builder import FeatureBuilder
from marketlens.main import create_app
from marketlens.models import (
    Backtest,
    ModelVersion,
    PipelineRun,
    PipelineRunStatus,
    Prediction,
    PredictionOutcome,
    Role,
)
from marketlens.repositories import feature_repo, ingestion_repo, price_repo, training_repo
from marketlens.services import auth as auth_service

N_DAYS = 120
START = pd.Timestamp("2024-01-01", tz="UTC")
PASSWORD = "correct horse battery staple"


@dataclass
class World:
    asset_id: int
    model_id: int  # production (baseline)
    candidate_id: int
    backtest_id: uuid.UUID
    failed_run_id: uuid.UUID
    last_day: datetime


@pytest.fixture
async def sm(migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", migrated_db)
    monkeypatch.setattr(settings, "redis_url", settings.redis_url.rsplit("/", 1)[0] + "/15")
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tables = ", ".join(t.name for t in reversed(Base.metadata.sorted_tables))
    async with maker() as s, s.begin():
        await s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield maker
    async with maker() as s, s.begin():
        await s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
async def client(sm: Any) -> AsyncIterator[AsyncClient]:
    app = create_app(get_settings())
    limiter.enabled = False  # rate-limit tests switch it on explicitly
    async with app.router.lifespan_context(app):
        await app.state.redis.flushdb()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c
        await app.state.redis.flushdb()
    limiter.enabled = True


def _metrics(days: pd.DatetimeIndex) -> dict[str, Any]:
    def entry(is_baseline: bool, loss: list[float], family: str) -> dict[str, Any]:
        return {
            "family": family,
            "is_baseline": is_baseline,
            "params": {},
            "summary": {"log_loss": {"mean": float(np.mean(loss)), "std": float(np.std(loss))}},
            "per_fold": {"log_loss": loss},
            "vs_best_baseline": {}
            if is_baseline
            else {
                "log_loss": {
                    "baseline": "historical_up_rate",
                    "diff_mean": 0.0002,
                    "wins": 2,
                    "folds": 4,
                    "significant": False,
                }
            },
            "eras": {},
            "importance": None,
            "calibration": [],
        }

    return {
        "primary_metric": "log_loss",
        "date_range": [days[0].isoformat(), days[-1].isoformat()],
        "folds": [{"index": i} for i in range(4)],
        "leaderboard": ["historical_up_rate", "logistic"],
        "verdict": {"best_model": "logistic", "beats_baseline": False},
        "models": {
            "historical_up_rate": entry(True, [0.693, 0.692, 0.694, 0.693], "baseline"),
            "logistic": entry(False, [0.690, 0.695, 0.691, 0.696], "linear"),
        },
    }


@pytest.fixture
async def world(sm: Any) -> World:
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, N_DAYS)))
    days = pd.date_range(START, periods=N_DAYS, freq="1D")
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
    await FeatureBuilder(sm).build("v1")
    async with sm() as s, s.begin():
        fs = await feature_repo.get_feature_set(s, "v1")
        assert fs is not None
        run_id = await training_repo.create_training_run(
            s,
            task="classification",
            feature_set_id=fs.id,
            asset_id=asset_id,
            split_config={"gap": 5},
            git_sha="abc",
            seed=42,
            metrics=_metrics(days),
        )
        await training_repo.finish_training_run(s, run_id, PipelineRunStatus.SUCCESS)
        ids = {}
        for algo in ("historical_up_rate", "logistic"):
            mv = await training_repo.create_model_version(
                s,
                name=f"btc-usd_1d_{algo}",
                algo=algo,
                task="classification",
                params={},
                feature_set_id=fs.id,
                training_run_id=run_id,
                train_start=days[0].to_pydatetime(),
                train_end=days[-1].to_pydatetime(),
                metrics={},
                mlflow_run_id=None,
                artifact_uri=None,
            )
            ids[algo] = mv.id
        await s.execute(
            update(ModelVersion)
            .where(ModelVersion.id == ids["historical_up_rate"])
            .values(stage="production")
        )
        last = days[-1].to_pydatetime()
        prev = Prediction(
            model_version_id=ids["historical_up_rate"],
            asset_id=asset_id,
            as_of_ts=last - timedelta(days=1),
            target_ts=last,
            horizon=1,
            y_pred=1.0,
            p_up=0.53,
            source="live",
        )
        live = Prediction(
            model_version_id=ids["historical_up_rate"],
            asset_id=asset_id,
            as_of_ts=last,
            target_ts=last + timedelta(days=1),
            horizon=1,
            y_pred=1.0,
            p_up=0.52,
            source="live",
        )
        s.add_all([prev, live])
        await s.flush()
        s.add(
            PredictionOutcome(
                prediction_id=prev.id,
                realized_return=0.01,
                realized_up=True,
                log_loss=-np.log(0.53),
                brier=0.47**2,
                correct=True,
            )
        )
        bt = Backtest(
            model_version_id=ids["logistic"],
            baseline_model_version_id=ids["historical_up_rate"],
            config={"fee_bps": 10, "slippage_bps": 5},
            git_sha="abc",
            equity_curve_uri="s3://backtests/x.parquet",
            equity_curve={
                "frequency": "weekly",
                "ts": ["2024-01-07T00:00:00+00:00", "2024-01-14T00:00:00+00:00"],
                "strategy_gross": [1.0, 1.03],
                "strategy_net": [1.0, 1.02],
                "buy_and_hold": [1.0, 1.05],
                "baseline": [1.0, 1.01],
            },
            metrics={
                "model": {"id": ids["logistic"], "name": "btc-usd_1d_logistic", "version": 1},
                "threshold": {"chosen": 0.52},
                "evaluation": {
                    "strategy_net": {
                        "sharpe": 0.5,
                        "total_return": 0.2,
                        "start": "2024-01-01",
                        "end": "2024-06-01",
                    },
                    "buy_and_hold": {"sharpe": 0.7, "total_return": 0.4},
                },
                "selection": {},
                "yearly": {},
                "sensitivity": {},
                "verdict": {"beats_buy_and_hold_sharpe": False},
                "disclaimer": "Hypothetical. Not financial advice.",
            },
        )
        s.add(bt)
        failed = PipelineRun(
            pipeline="task:ingest",
            status=PipelineRunStatus.FAILED,
            params={
                "asset": "BTC-USD",
                "source": "kaggle_btc",
                "mode": "incremental",
                "refresh": False,
            },
            error="TransientError: down",
            attempt=6,
        )
        s.add(failed)
        await s.flush()
        return World(asset_id, ids["historical_up_rate"], ids["logistic"], bt.id, failed.id, last)


async def make_user(sm: Any, email: str, role: Role = Role.USER) -> None:
    async with sm() as s, s.begin():
        user = await auth_service.register(s, email, PASSWORD)
        user.role = role


async def login(client: AsyncClient, email: str) -> dict[str, str]:
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return dict(r.json())


def bearer(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
