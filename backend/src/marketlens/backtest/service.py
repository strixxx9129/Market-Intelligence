"""Backtest service: one model version's out-of-sample predictions → hypothetical strategy.

    folds 0 … k-1  (selection)   choose the threshold by net Sharpe
    folds k … n-1  (evaluation)  apply it, report it, against:
        - buy-and-hold (always long, same execution & costs)
        - the baseline strategy (best baseline model of the same training run, same rule)
        - cash (0%)
    sensitivity: the other execution timing; a sweep of cost levels.

Everything uses OOS predictions only: each prediction was made by a model trained on data
before that day. Educational, hypothetical results, not financial advice.
"""

import io
import math
import uuid
from dataclasses import dataclass
from typing import Any

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.backtest.curves import downsample_curve
from marketlens.backtest.engine import (
    BacktestConfig,
    BacktestResult,
    Execution,
    PredictionKind,
    run_backtest,
    signals_from_predictions,
)
from marketlens.backtest.metrics import performance_metrics, yearly_returns
from marketlens.backtest.selection import select_threshold
from marketlens.core.config import get_settings
from marketlens.core.logging import get_logger
from marketlens.core.storage import ObjectStore, S3ObjectStore
from marketlens.ml.train import json_safe
from marketlens.repositories import backtest_repo, price_repo

logger = get_logger(__name__)
COST_SWEEP_BPS = (0.0, 5.0, 15.0, 30.0, 60.0)  # total (fee + slippage) per unit traded
DISCLAIMER = "Hypothetical backtest on historical data. Educational only, not financial advice."


@dataclass(frozen=True)
class BacktestRequest:
    model_version_id: int
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    execution: Execution = "next_open"
    long_short: bool = False
    vol_target: float | None = None
    selection_fraction: float = 0.5  # share of folds used to choose the threshold


@dataclass
class StrategyRun:
    threshold: float
    selection_table: list[dict[str, float]]
    selection: BacktestResult
    evaluation: BacktestResult


def sharpe_standard_error(sharpe: float, days: int) -> float:
    """Approximate standard error of an annualised Sharpe ratio (Lo, 2002), i.i.d. returns:
    SE ≈ sqrt((1 + SR²/2) / years). Two strategies within ~2 SE are indistinguishable."""
    years = days / 365
    return (
        math.sqrt((1 + 0.5 * sharpe**2) / years)
        if years > 0 and math.isfinite(sharpe)
        else math.nan
    )


def _metrics(result: BacktestResult, gross: bool = False) -> dict[str, Any]:
    d = result.daily
    returns = d["gross_return"] if gross else d["net_return"]
    m = performance_metrics(
        returns, d["position"], d["turnover"], None if gross else d["cost"], result.trades
    )
    m["sharpe_se"] = sharpe_standard_error(m["sharpe"], m["days"])
    return m


def run_strategy(
    preds: pd.Series,
    prices: pd.DataFrame,
    selection_days: pd.Index,
    evaluation_days: pd.Index,
    cfg: BacktestConfig,
    kind: PredictionKind,
) -> StrategyRun:
    chosen = select_threshold(preds.loc[selection_days], prices, cfg, kind)
    final = cfg.replace(threshold=chosen.threshold)
    signal = signals_from_predictions(preds, kind, final.threshold, final.long_short)
    sel = run_backtest(signal.loc[selection_days], prices.loc[: selection_days.max()], final)
    ev = run_backtest(signal.loc[evaluation_days], prices, final)
    return StrategyRun(chosen.threshold, chosen.table, sel, ev)


class Backtester:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        store: ObjectStore | None = None,
        git_sha: str | None = None,
    ) -> None:
        settings = get_settings()
        self.sessionmaker = sessionmaker
        self.store = store or S3ObjectStore(settings.backtest_bucket)
        self.git_sha = git_sha or settings.git_sha

    async def run(self, req: BacktestRequest) -> tuple[uuid.UUID, dict[str, Any], str]:
        async with self.sessionmaker() as session:
            mv = await backtest_repo.get_model_version(session, req.model_version_id)
            if mv is None:
                raise LookupError(f"model version {req.model_version_id} not found")
            run = await backtest_repo.get_training_run(session, mv.training_run_id)
            assert run is not None
            entry = run.metrics["models"][mv.algo]
            primary = run.metrics["primary_metric"]
            baseline_algo = (
                mv.algo if entry["is_baseline"] else entry["vs_best_baseline"][primary]["baseline"]
            )
            baseline_mv = await backtest_repo.find_version(session, run.id, baseline_algo)
            assert baseline_mv is not None
            preds = await backtest_repo.load_predictions(session, mv.id)
            base_preds = await backtest_repo.load_predictions(session, baseline_mv.id)
            prices = await price_repo.load_candles(session, run.asset_id, "1d")
        if preds.empty:
            raise ValueError(f"model version {mv.id} has no out-of-sample predictions")

        kind: PredictionKind = "proba" if mv.task == "classification" else "return"
        column = "proba" if kind == "proba" else "y_pred"
        n_folds = int(preds["fold"].max()) + 1
        cut = max(1, int(n_folds * req.selection_fraction))
        if cut >= n_folds:
            raise ValueError("need at least one evaluation fold after the selection folds")
        selection_days = preds.index[preds["fold"] < cut]
        evaluation_days = preds.index[preds["fold"] >= cut]

        cfg = BacktestConfig(
            execution=req.execution,
            fee_bps=req.fee_bps,
            slippage_bps=req.slippage_bps,
            long_short=req.long_short,
            vol_target=req.vol_target,
        )
        strat = run_strategy(preds[column], prices, selection_days, evaluation_days, cfg, kind)
        base = run_strategy(base_preds[column], prices, selection_days, evaluation_days, cfg, kind)
        final = cfg.replace(threshold=strat.threshold)
        ones = pd.Series(1.0, index=evaluation_days)
        bh = run_backtest(ones, prices, cfg)
        bh_selection = run_backtest(
            pd.Series(1.0, index=selection_days), prices.loc[: selection_days.max()], cfg
        )

        # Sensitivity on the evaluation period, threshold fixed.
        signal = signals_from_predictions(preds[column], kind, final.threshold, final.long_short)
        other_exec: Execution = "next_close" if req.execution == "next_open" else "next_open"
        sensitivity = {
            "execution": {
                req.execution: _metrics(strat.evaluation),
                other_exec: _metrics(
                    run_backtest(
                        signal.loc[evaluation_days], prices, final.replace(execution=other_exec)
                    )
                ),
            },
            "costs_bps": {
                str(int(c)): _metrics(
                    run_backtest(
                        signal.loc[evaluation_days],
                        prices,
                        final.replace(fee_bps=c, slippage_bps=0.0),
                    )
                )
                for c in COST_SWEEP_BPS
            },
            "buy_and_hold_costs_bps": {
                str(int(c)): _metrics(
                    run_backtest(ones, prices, cfg.replace(fee_bps=c, slippage_bps=0.0))
                )
                for c in COST_SWEEP_BPS
            },
        }

        ev = strat.evaluation.daily
        metrics: dict[str, Any] = {
            "disclaimer": DISCLAIMER,
            "model": {
                "id": mv.id,
                "name": mv.name,
                "version": mv.version,
                "algo": mv.algo,
                "task": mv.task,
            },
            "baseline_model": {"id": baseline_mv.id, "algo": baseline_algo},
            "prediction_kind": kind,
            "folds": {
                "total": n_folds,
                "selection": list(range(cut)),
                "evaluation": list(range(cut, n_folds)),
            },
            "threshold": {"chosen": strat.threshold, "selection_table": strat.selection_table},
            "baseline_threshold": base.threshold,
            "evaluation": {
                "strategy_net": _metrics(strat.evaluation),
                "strategy_gross": _metrics(strat.evaluation, gross=True),
                "buy_and_hold": _metrics(bh),
                "baseline_strategy": _metrics(base.evaluation),
                "cash": {"total_return": 0.0, "cagr": 0.0, "sharpe": None, "max_drawdown": 0.0},
            },
            "selection": {
                "strategy_net": _metrics(strat.selection),
                "buy_and_hold": _metrics(bh_selection),
            },
            "yearly": {
                "strategy_net": yearly_returns(ev["net_return"]),
                "buy_and_hold": yearly_returns(bh.daily["net_return"]),
                "baseline_strategy": yearly_returns(base.evaluation.daily["net_return"]),
            },
            "sensitivity": sensitivity,
        }
        s, b = metrics["evaluation"]["strategy_net"], metrics["evaluation"]["buy_and_hold"]
        metrics["verdict"] = {
            "beats_buy_and_hold_sharpe": bool(s["sharpe"] > b["sharpe"]),
            "beats_buy_and_hold_cagr": bool(s["cagr"] > b["cagr"]),
            "beats_baseline_sharpe": bool(
                s["sharpe"] > metrics["evaluation"]["baseline_strategy"]["sharpe"]
            ),
            "sharpe_difference_vs_bh": s["sharpe"] - b["sharpe"],
            "sharpe_se": s["sharpe_se"],
            "selection_vs_evaluation_sharpe": [
                metrics["selection"]["strategy_net"]["sharpe"],
                s["sharpe"],
            ],
        }

        backtest_id = uuid.uuid4()
        curve = pd.DataFrame(
            {
                "fold": preds["fold"].reindex(ev.index).ffill(),
                "position": ev["position"],
                "gross_return": ev["gross_return"],
                "cost": ev["cost"],
                "net_return": ev["net_return"],
                "equity_gross": ev["equity_gross"],
                "equity_net": ev["equity_net"],
                "equity_buy_and_hold": bh.daily["equity_net"].reindex(ev.index),
                "equity_baseline": base.evaluation.daily["equity_net"].reindex(ev.index),
            }
        )
        buffer = io.BytesIO()
        curve.to_parquet(buffer)
        uri = self.store.put_bytes(
            f"{backtest_id}.parquet", buffer.getvalue(), "application/vnd.apache.parquet"
        )

        config = {
            **req.__dict__,
            "threshold": strat.threshold,
            "cost_rate": final.cost_rate,
            "training_run_id": str(run.id),
        }
        async with self.sessionmaker() as session, session.begin():
            await backtest_repo.create_backtest(
                session,
                id=backtest_id,
                model_version_id=mv.id,
                baseline_model_version_id=baseline_mv.id,
                config=json_safe(config),
                metrics=json_safe(metrics),
                equity_curve_uri=uri,
                equity_curve=downsample_curve(curve),
                git_sha=self.git_sha,
            )
        logger.info(
            "backtest_finished",
            backtest_id=str(backtest_id),
            model=mv.name,
            threshold=strat.threshold,
            sharpe=s["sharpe"],
            bh_sharpe=b["sharpe"],
        )
        return backtest_id, metrics, uri
