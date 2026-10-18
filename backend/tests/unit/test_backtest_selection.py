import numpy as np
import pandas as pd

from marketlens.backtest.engine import BacktestConfig, run_backtest, signals_from_predictions
from marketlens.backtest.metrics import performance_metrics
from marketlens.backtest.selection import PROBA_GRID, select_threshold


def _market(n: int = 800, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n)))
    open_ = np.concatenate([[100.0], close[:-1]])  # 24/7 market: open[t+1] = close[t]
    idx = pd.date_range("2020-01-01", periods=n, freq="1D", tz="UTC", name="ts")
    return pd.DataFrame({"open": open_, "close": close}, index=idx)


def _oracle(prices: pd.DataFrame, noise: float = 0.0, seed: int = 1) -> pd.Series:
    """Cheating 'model' for testing the engine: P(up) leans towards tomorrow's real direction."""
    rng = np.random.default_rng(seed)
    nxt = np.sign(np.log(prices["close"].shift(-1) / prices["close"]))
    return (0.5 + 0.1 * nxt + rng.normal(0, noise, len(prices))).iloc[:-1]


def test_selection_ignores_evaluation_period_prices() -> None:
    prices = _market()
    rng = np.random.default_rng(5)
    preds = pd.Series(rng.uniform(0.4, 0.6, len(prices) - 5), index=prices.index[:-5])
    selection = preds.iloc[:400]
    cfg = BacktestConfig()

    first = select_threshold(selection, prices, cfg, "proba")
    tampered = prices.copy()
    tampered.iloc[401:] *= np.exp(np.cumsum(rng.normal(0, 0.1, len(prices) - 401)))[:, None]
    second = select_threshold(selection, tampered, cfg, "proba")

    assert first.threshold == second.threshold
    assert first.table == second.table  # not even the last selection trade saw the future
    assert [r["threshold"] for r in first.table] == list(PROBA_GRID)


def test_ties_prefer_the_neutral_threshold() -> None:
    prices = _market(200)
    preds = pd.Series(0.9, index=prices.index[:150])  # every threshold gives "always long"
    assert select_threshold(preds, prices, BacktestConfig(), "proba").threshold == 0.5


def test_oracle_needs_next_open_execution_to_capture_the_predicted_day() -> None:
    """Positive control for the timing model: predictions about day t+1 pay off only when
    executed at open[t+1] (≈ close[t]). Executing at close[t+1] trades the day after."""
    prices = _market()
    signal = signals_from_predictions(_oracle(prices), "proba", 0.5, False)
    zero = BacktestConfig(fee_bps=0, slippage_bps=0)
    fast = run_backtest(signal, prices, zero).daily
    slow = run_backtest(signal, prices, zero.replace(execution="next_close")).daily
    sharpe_fast = performance_metrics(fast["net_return"])["sharpe"]
    sharpe_slow = performance_metrics(slow["net_return"])["sharpe"]
    assert sharpe_fast > 10  # perfect foresight of tomorrow, captured
    assert abs(sharpe_slow) < 2  # one day late: the foresight is worthless
