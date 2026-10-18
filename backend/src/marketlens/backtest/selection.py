"""Choosing the signal threshold without peeking at the reported period.

Trying 11 thresholds and reporting the best one on the same data is a hidden fit: the "best"
partly reflects luck in that period, so its result is biased upwards. Instead:

    selection folds (earlier)  →  pick the threshold with the best net Sharpe
    evaluation folds (later)   →  apply it unchanged and report THAT

Prices are cut at the last selection decision day, so even the final selection trade cannot
peek at evaluation-period prices. The gap between the selection Sharpe and the evaluation
Sharpe shows how much of the in-sample result was optimism.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from marketlens.backtest.engine import (
    BacktestConfig,
    PredictionKind,
    run_backtest,
    signals_from_predictions,
)
from marketlens.backtest.metrics import performance_metrics

PROBA_GRID = tuple(round(0.50 + 0.01 * i, 2) for i in range(11))  # 0.50 … 0.60
RETURN_GRID = (0.0, 0.0005, 0.001, 0.002, 0.003, 0.005)


@dataclass
class Selection:
    threshold: float
    table: list[dict[str, float]]  # per candidate: threshold, sharpe, cagr, exposure, trades


def default_grid(kind: PredictionKind) -> tuple[float, ...]:
    return PROBA_GRID if kind == "proba" else RETURN_GRID


def select_threshold(
    predictions: pd.Series,
    prices: pd.DataFrame,
    cfg: BacktestConfig,
    kind: PredictionKind,
    grid: tuple[float, ...] | None = None,
) -> Selection:
    """Best net Sharpe on the selection predictions only. Ties → the more neutral threshold
    (closer to 0.5 / 0), i.e. the least "tuned" choice."""
    grid = grid or default_grid(kind)
    cut_prices = prices.loc[: predictions.index.max()]  # no evaluation-period prices at all
    neutral = 0.5 if kind == "proba" else 0.0
    rows = []
    for thr in grid:
        signal = signals_from_predictions(predictions, kind, thr, cfg.long_short).dropna()
        daily = run_backtest(signal, cut_prices, cfg.replace(threshold=thr)).daily
        m = performance_metrics(daily["net_return"], daily["position"], daily["turnover"])
        sharpe = m["sharpe"] if np.isfinite(m["sharpe"]) else -np.inf
        rows.append(
            {
                "threshold": thr,
                "sharpe": float(sharpe),
                "cagr": float(m["cagr"]),
                "exposure": float(m["exposure"]),
                "turnover_per_year": float(m["turnover_per_year"]),
            }
        )
    best = max(rows, key=lambda r: (r["sharpe"], -abs(r["threshold"] - neutral)))
    return Selection(threshold=float(best["threshold"]), table=rows)
