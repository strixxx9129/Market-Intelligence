"""Performance metrics for daily strategy returns (365 periods per year: crypto never closes).

- CAGR: the constant yearly growth rate that turns 1 into the final equity.
- Annualised volatility: std of daily returns x sqrt(365). How bumpy the ride is.
- Sharpe: mean / std of daily returns x sqrt(365), risk-free rate 0. Return per unit of risk.
- Sortino: like Sharpe but only downside moves count as risk (upside surprises aren't "risk").
- Max drawdown: worst fall from a previous equity peak. Duration: longest time below a peak.
- Turnover: position traded per year (1.0 = one full in-or-out). Drives costs.
- Hit rate: share of in-market days with a positive net return; trade win rate per trade.
- Exposure: share of days with a position (and the average absolute position size).
"""

from typing import Any

import numpy as np
import pandas as pd

from marketlens.backtest.engine import DAYS_PER_YEAR


def drawdown_stats(net_returns: pd.Series) -> dict[str, Any]:
    """Max drawdown, its length and whether it recovered. Starting capital (1.0) counts as the
    first peak, so losing from day one is a drawdown too."""
    equity = np.concatenate([[1.0], (1 + net_returns.to_numpy()).cumprod()])
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1
    trough = int(np.argmin(dd))
    peak_at = int(np.argmax(equity[: trough + 1])) if trough > 0 else 0

    longest, start = 0, None
    for i, value in enumerate(dd):
        if value < 0 and start is None:
            start = i - 1  # index of the peak we fell from
        elif value >= 0 and start is not None:
            longest, start = max(longest, i - start), None
    open_spell = len(dd) - 1 - start if start is not None else 0
    return {
        "max_drawdown": float(dd.min()),
        "max_drawdown_peak": peak_at,
        "max_drawdown_trough": trough,
        "max_drawdown_duration_days": int(max(longest, open_spell)),
        "in_drawdown_at_end": bool(dd[-1] < 0),
    }


def performance_metrics(
    net_returns: pd.Series,
    position: pd.Series | None = None,
    turnover: pd.Series | None = None,
    cost: pd.Series | None = None,
    trades: pd.DataFrame | None = None,
) -> dict[str, Any]:
    r = net_returns.astype(float)
    n = len(r)
    if n == 0:
        return {"days": 0}
    years = n / DAYS_PER_YEAR
    final = float((1 + r).prod())
    std = float(r.std(ddof=1)) if n > 1 else float("nan")
    downside = float(np.sqrt(np.mean(np.minimum(r, 0.0) ** 2)))
    out: dict[str, Any] = {
        "days": n,
        "start": r.index[0].isoformat(),
        "end": r.index[-1].isoformat(),
        "total_return": final - 1,
        "cagr": final ** (1 / years) - 1 if final > 0 else -1.0,
        "volatility": std * np.sqrt(DAYS_PER_YEAR),
        "sharpe": float(r.mean() / std * np.sqrt(DAYS_PER_YEAR)) if std > 0 else float("nan"),
        "sortino": (
            float(r.mean() / downside * np.sqrt(DAYS_PER_YEAR)) if downside > 0 else float("nan")
        ),
        **drawdown_stats(r),
    }
    if position is not None:
        in_market = position.abs() > 0
        out["exposure"] = float(in_market.mean())
        out["avg_abs_position"] = float(position.abs().mean())
        out["hit_rate"] = float((r[in_market] > 0).mean()) if in_market.any() else float("nan")
    if turnover is not None:
        out["turnover_per_year"] = float(turnover.sum() / years)
    if cost is not None:
        out["total_cost"] = float(cost.sum())
    if trades is not None:
        out["trades"] = len(trades)
        out["trade_win_rate"] = (
            float((trades["return"] > 0).mean()) if len(trades) else float("nan")
        )
        out["avg_trade_days"] = float(trades["days"].mean()) if len(trades) else float("nan")
    return out


def yearly_returns(net_returns: pd.Series) -> dict[str, float]:
    grouped = (1 + net_returns).groupby(net_returns.index.year).prod() - 1
    return {str(year): float(v) for year, v in grouped.items()}
