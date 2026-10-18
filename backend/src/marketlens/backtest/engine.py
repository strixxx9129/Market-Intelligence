"""Vectorised daily backtest engine. Educational: hypothetical results, not financial advice.

Timeline for a decision made on day t (prediction from features of day t, known after the
close of day t):

    day t        close[t] → signal computed      (we can NOT trade at this price anymore)
    day t+1      executed at the execution price  open[t+1]  (next_open)  or  close[t+1]
    holding      earns exec[t+1] → exec[t+2], then the next decision takes over

Implementation: `exec_return[k] = exec[k+1] / exec[k] - 1` is the return of holding from
execution day k to k+1, and `position[k] = target[k-1]`. That single `shift(1)` guarantees
a signal can never earn the price move it was computed from.

Costs: (fee_bps + slippage_bps) / 1e4 per unit of position traded, charged on the execution
day; flipping long→short trades 2 units. An open position at the end is marked to market
(no exit cost). Shorts are simple (no borrow fee or funding).
"""

from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

Execution = Literal["next_open", "next_close"]
PredictionKind = Literal["proba", "return"]
DAYS_PER_YEAR = 365  # crypto trades every day


@dataclass(frozen=True)
class BacktestConfig:
    threshold: float = 0.5
    long_short: bool = False
    execution: Execution = "next_open"
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    vol_target: float | None = None  # annualised, e.g. 0.5 = 50%
    vol_lookback: int = 30
    max_leverage: float = 1.0

    @property
    def cost_rate(self) -> float:
        return (self.fee_bps + self.slippage_bps) / 10_000

    def replace(self, **changes: Any) -> "BacktestConfig":
        return BacktestConfig(**{**asdict(self), **changes})

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BacktestResult:
    daily: pd.DataFrame  # index = execution day
    trades: pd.DataFrame


def signals_from_predictions(
    predictions: pd.Series, kind: PredictionKind, threshold: float, long_short: bool
) -> pd.Series:
    """Map predictions (indexed by decision day) to target positions in {-1, 0, 1}.

    proba : long if P(up) > threshold; short if long_short and P(up) < 1 - threshold.
    return: long if predicted return > threshold; short if long_short and < -threshold.
    """
    p = predictions.astype(float)
    if kind == "proba":
        long, short = p > threshold, p < 1 - threshold
    else:
        long, short = p > threshold, p < -threshold
    signal = pd.Series(np.where(long, 1.0, 0.0), index=p.index)
    if long_short:
        signal[short & ~long] = -1.0
    return signal.where(p.notna())


def realised_volatility(prices: pd.DataFrame, lookback: int) -> pd.Series:
    """Annualised std of close-to-close log returns over the last `lookback` days, using
    data up to and including day t only (a decision-day quantity, no look-ahead)."""
    log_ret = np.log(prices["close"] / prices["close"].shift(1))
    return log_ret.rolling(lookback).std() * np.sqrt(DAYS_PER_YEAR)


def run_backtest(target: pd.Series, prices: pd.DataFrame, cfg: BacktestConfig) -> BacktestResult:
    """Simulate holding `target[t]` (decided on day t) from the next execution price on.

    `prices`: daily candles with `open` and `close`, indexed by day (UTC), sorted.
    Only execution days whose holding period is fully covered by prices are booked.
    """
    if target.empty:
        raise ValueError("no signals to backtest")
    prices = prices.sort_index()
    exec_price = prices["open"] if cfg.execution == "next_open" else prices["close"]
    exec_return = exec_price.shift(-1) / exec_price - 1  # hold from exec day k to k+1

    sized = target.reindex(prices.index)
    if cfg.vol_target is not None:
        vol = realised_volatility(prices, cfg.vol_lookback)
        scale = (cfg.vol_target / vol).clip(upper=cfg.max_leverage)
        sized = sized * scale

    position = sized.shift(1)  # THE look-ahead guard: decided at k-1, held from k
    first_exec = prices.index.get_loc(target.index.min()) + 1
    last_exec = prices.index.get_loc(target.index.max()) + 1
    window = prices.index[first_exec : last_exec + 1]
    frame = pd.DataFrame(
        {"position": position.reindex(window), "exec_return": exec_return.reindex(window)}
    )
    frame = frame[frame["exec_return"].notna()]  # need the next execution price to book it
    frame["position"] = frame["position"].fillna(0.0)  # no prediction (or vol warm-up) = flat

    prev = frame["position"].shift(1, fill_value=0.0)  # start flat: entering costs too
    frame["turnover"] = (frame["position"] - prev).abs()
    frame["cost"] = frame["turnover"] * cfg.cost_rate
    frame["gross_return"] = frame["position"] * frame["exec_return"]
    frame["net_return"] = frame["gross_return"] - frame["cost"]
    frame["equity_gross"] = (1 + frame["gross_return"]).cumprod()
    frame["equity_net"] = (1 + frame["net_return"]).cumprod()
    frame.index.name = "ts"
    return BacktestResult(daily=frame, trades=extract_trades(frame, exec_price, cfg.cost_rate))


def extract_trades(daily: pd.DataFrame, exec_price: pd.Series, cost_rate: float) -> pd.DataFrame:
    """One trade per contiguous run of same-direction exposure (size changes from volatility
    targeting don't start a new trade).

    Each trade carries its OWN costs: entry (rate x |first size|), any rebalancing inside the
    trade, and exit (rate x |last size|, which the daily ledger books on the next day). Summed
    over trades this equals the strategy's total cost (exact for long/flat; on long↔short flips
    the split of that day's cost is exact only to second order).
    """
    direction = np.sign(daily["position"])
    run_id = (direction != direction.shift()).cumsum()
    trades = []
    for _, run in daily.groupby(run_id):
        side = float(np.sign(run["position"].iloc[0]))
        if side == 0:
            continue
        entry, last = run.index[0], run.index[-1]
        exit_day = exec_price.index[exec_price.index.get_loc(last) + 1]
        cost = run["cost"].copy()
        cost.iloc[0] = cost_rate * abs(run["position"].iloc[0])  # own entry only
        growth = (1 + run["gross_return"] - cost).prod()
        exit_cost = cost_rate * abs(run["position"].iloc[-1]) if exit_day in daily.index else 0.0
        trades.append(
            {
                "entry": entry,
                "exit": exit_day,
                "direction": "long" if side > 0 else "short",
                "entry_price": float(exec_price[entry]),
                "exit_price": float(exec_price[exit_day]),
                "days": len(run),
                "return": float(growth * (1 - exit_cost) - 1),
            }
        )
    columns = ["entry", "exit", "direction", "entry_price", "exit_price", "days", "return"]
    return pd.DataFrame(trades, columns=columns)
