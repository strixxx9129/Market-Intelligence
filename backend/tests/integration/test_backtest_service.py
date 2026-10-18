import io
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.backtest.report import render_backtest_report
from marketlens.backtest.service import Backtester, BacktestRequest
from marketlens.core.storage import LocalObjectStore
from marketlens.db.seed import seed_assets
from marketlens.models import Backtest
from marketlens.repositories import feature_repo, ingestion_repo, price_repo, training_repo

Sessionmaker = async_sessionmaker[AsyncSession]
N_DAYS, N_FOLDS, FOLD_LEN, START = 900, 8, 100, 50


async def _setup(sm: Sessionmaker, informative: bool) -> tuple[int, int]:
    """Prices + a training run with a model version and its baseline, each with OOS preds."""
    rng = np.random.default_rng(4)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, N_DAYS)))
    open_ = np.concatenate([[100.0], close[:-1]])
    days = pd.date_range("2018-01-01", periods=N_DAYS, freq="1D", tz="UTC")
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
                    "open": price_repo.to_decimal(o),
                    "high": price_repo.to_decimal(max(o, c) * 1.01),
                    "low": price_repo.to_decimal(min(o, c) * 0.99),
                    "close": price_repo.to_decimal(c),
                    "volume": price_repo.to_decimal(1000),
                    "source": "test",
                }
                for d, o, c in zip(days, open_, close, strict=True)
            ],
        )
        fs = await feature_repo.create_feature_set(s, "v1", {"features": []}, "0" * 64)
        metrics = {
            "primary_metric": "log_loss",
            "models": {
                "historical_up_rate": {"is_baseline": True, "vs_best_baseline": {}},
                "logistic": {
                    "is_baseline": False,
                    "vs_best_baseline": {"log_loss": {"baseline": "historical_up_rate"}},
                },
            },
        }
        run_id = await training_repo.create_training_run(
            s,
            task="classification",
            feature_set_id=fs.id,
            asset_id=asset_id,
            split_config={},
            git_sha="test",
            seed=1,
            metrics=metrics,
        )
        ids = {}
        nxt = np.sign(np.log(np.roll(close, -1) / close))
        for algo in ("historical_up_rate", "logistic"):
            mv = await training_repo.create_model_version(
                s,
                name=f"btc_{algo}",
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
            test_days = days[START : START + N_FOLDS * FOLD_LEN]
            if algo == "logistic" and informative:
                proba = 0.5 + 0.05 * nxt[START : START + N_FOLDS * FOLD_LEN]  # oracle-ish
            elif algo == "logistic":
                proba = rng.uniform(0.45, 0.55, len(test_days))
            else:
                proba = np.full(len(test_days), 0.53)
            oos = pd.DataFrame(
                {
                    "fold": np.repeat(range(N_FOLDS), FOLD_LEN),
                    "y_true": 0.0,
                    "y_pred": (proba > 0.5).astype(float),
                    "proba": proba,
                },
                index=test_days,
            )
            await training_repo.insert_oos_predictions(s, run_id, mv.id, oos)
    return ids["logistic"], ids["historical_up_rate"]


@pytest.mark.parametrize("informative", [True, False])
async def test_backtest_is_stored_with_curve_and_honest_periods(
    db_sessionmaker: Sessionmaker, tmp_path: Path, informative: bool
) -> None:
    model_id, baseline_id = await _setup(db_sessionmaker, informative)
    bt_id, m, uri = await Backtester(
        db_sessionmaker, store=LocalObjectStore(tmp_path), git_sha="abc"
    ).run(BacktestRequest(model_version_id=model_id))

    assert m["folds"]["selection"] == [0, 1, 2, 3] and m["folds"]["evaluation"] == [4, 5, 6, 7]
    ev = m["evaluation"]
    eval_start = pd.Timestamp("2018-01-01", tz="UTC") + pd.Timedelta(days=START + 4 * FOLD_LEN)
    assert pd.Timestamp(ev["strategy_net"]["start"]) == eval_start + pd.Timedelta(days=1)
    assert ev["strategy_gross"]["total_return"] >= ev["strategy_net"]["total_return"]
    assert set(m["sensitivity"]["costs_bps"]) == {"0", "5", "15", "30", "60"}
    assert m["baseline_model"]["id"] == baseline_id
    if informative:  # positive control: a model that knows tomorrow must beat buy & hold
        assert m["verdict"]["beats_buy_and_hold_sharpe"]
        nxt_close = m["sensitivity"]["execution"]["next_close"]["sharpe"]
        assert nxt_close < ev["strategy_net"]["sharpe"]  # one day late kills it

    async with db_sessionmaker() as s:
        row = await s.get(Backtest, bt_id)
    assert row is not None and row.equity_curve_uri == uri and row.git_sha == "abc"
    assert row.config["threshold"] == m["threshold"]["chosen"]
    curve = pd.read_parquet(io.BytesIO(LocalObjectStore(tmp_path).get_bytes(uri)))
    assert curve["equity_net"].iloc[-1] == pytest.approx(1 + ev["strategy_net"]["total_return"])
    assert {"equity_buy_and_hold", "equity_baseline", "position", "fold"} <= set(curve.columns)
    assert curve["fold"].min() >= 4


async def test_report_renders_and_states_the_verdict(
    db_sessionmaker: Sessionmaker, tmp_path: Path
) -> None:
    model_id, _ = await _setup(db_sessionmaker, informative=False)
    backtester = Backtester(db_sessionmaker, store=LocalObjectStore(tmp_path), git_sha="abc")
    await backtester.run(BacktestRequest(model_version_id=model_id))
    async with db_sessionmaker() as s:
        rows = (await s.scalars(select(Backtest))).all()
    md = render_backtest_report(list(rows))
    assert "not financial advice" in md and "## Summary" in md and "Metric glossary" in md
    assert "Selection vs evaluation" in md


async def test_unknown_model_version(db_sessionmaker: Sessionmaker, tmp_path: Path) -> None:
    with pytest.raises(LookupError, match="not found"):
        await Backtester(db_sessionmaker, store=LocalObjectStore(tmp_path)).run(
            BacktestRequest(model_version_id=999_999)
        )
