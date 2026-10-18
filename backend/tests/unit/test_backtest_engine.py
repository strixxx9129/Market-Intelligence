import numpy as np
import pandas as pd
import pytest

from marketlens.backtest.engine import (
    BacktestConfig,
    realised_volatility,
    run_backtest,
    signals_from_predictions,
)
from marketlens.backtest.metrics import drawdown_stats, performance_metrics, yearly_returns

ZERO_COST = BacktestConfig(fee_bps=0, slippage_bps=0)


def _prices(n: int = 300, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.03, n)))
    open_ = np.concatenate([[100.0], close[:-1]]) * (1 + rng.normal(0, 0.002, n))
    idx = pd.date_range("2021-01-01", periods=n, freq="1D", tz="UTC", name="ts")
    return pd.DataFrame({"open": open_, "close": close}, index=idx)


def _ones(prices: pd.DataFrame, start: int = 10, end: int = -10) -> pd.Series:
    return pd.Series(1.0, index=prices.index[start:end])


# ---------------------------------------------------------------- required: buy-and-hold
@pytest.mark.parametrize("execution", ["next_open", "next_close"])
def test_always_long_with_zero_costs_equals_buy_and_hold(execution: str) -> None:
    prices = _prices()
    target = _ones(prices)
    cfg = ZERO_COST.replace(execution=execution)
    daily = run_backtest(target, prices, cfg).daily

    col = "open" if execution == "next_open" else "close"
    first, last = daily.index[0], daily.index[-1]
    exit_day = prices.index[prices.index.get_loc(last) + 1]
    buy_and_hold = prices.loc[exit_day, col] / prices.loc[first, col]
    assert daily["equity_net"].iloc[-1] == pytest.approx(buy_and_hold, rel=1e-12)
    assert (daily["position"] == 1).all() and daily["cost"].sum() == 0


# ---------------------------------------------------------------- required: no same-close fills
@pytest.mark.parametrize("execution", ["next_open", "next_close"])
def test_never_trades_at_the_close_the_signal_was_computed_from(execution: str) -> None:
    """Day 50 closes +100%. A signal on day 50 'knows' that close; it must not earn it."""
    idx = pd.date_range("2022-01-01", periods=100, freq="1D", tz="UTC", name="ts")
    close = np.where(np.arange(100) >= 50, 200.0, 100.0)
    prices = pd.DataFrame(
        {"open": np.concatenate([[100.0], close[:-1]]), "close": close}, index=idx
    )
    target = pd.Series(0.0, index=idx[:90])
    target.iloc[50] = 1.0  # the "perfect hindsight" signal, decided after day 50's close

    daily = run_backtest(target, prices, ZERO_COST.replace(execution=execution)).daily

    assert daily["position"].loc[idx[50]] == 0  # not in the market on the signal day
    assert daily["position"].loc[idx[51]] == 1  # executed the next day
    assert daily["equity_net"].iloc[-1] == pytest.approx(1.0)  # the jump is NOT captured


def test_position_is_the_previous_days_signal() -> None:
    prices = _prices(60)
    rng = np.random.default_rng(1)
    target = pd.Series(rng.integers(0, 2, 40).astype(float), index=prices.index[5:45])
    daily = run_backtest(target, prices, ZERO_COST).daily
    expected = target.shift(1).reindex(daily.index).fillna(0.0)
    pd.testing.assert_series_equal(daily["position"], expected, check_names=False)


# ---------------------------------------------------------------- required: costs vs turnover
def test_costs_reduce_returns_in_proportion_to_turnover() -> None:
    prices = _prices()
    rng = np.random.default_rng(2)
    target = pd.Series(rng.integers(0, 2, 250).astype(float), index=prices.index[20:270])
    gross = run_backtest(target, prices, ZERO_COST).daily
    cfg = BacktestConfig(fee_bps=10, slippage_bps=5)
    net = run_backtest(target, prices, cfg).daily

    drag = gross["gross_return"] - net["net_return"]
    np.testing.assert_allclose(drag, net["turnover"] * 0.0015, atol=1e-15)
    double = run_backtest(target, prices, BacktestConfig(fee_bps=20, slippage_bps=10)).daily
    np.testing.assert_allclose(gross["gross_return"] - double["net_return"], 2 * drag, atol=1e-15)
    assert net["turnover"].sum() > 50  # the test is meaningful: lots of trading


def test_long_short_flip_trades_two_units() -> None:
    prices = _prices(30)
    target = pd.Series([1.0] * 5 + [-1.0] * 5, index=prices.index[:10])
    daily = run_backtest(target, prices, BacktestConfig(long_short=True)).daily
    assert daily["turnover"].iloc[0] == 1  # flat -> long
    assert daily["turnover"].iloc[5] == 2  # long -> short


def test_buy_and_hold_pays_entry_cost_once() -> None:
    prices = _prices()
    daily = run_backtest(_ones(prices), prices, BacktestConfig(fee_bps=10, slippage_bps=5)).daily
    assert daily["cost"].sum() == pytest.approx(0.0015)


# ---------------------------------------------------------------- signals, vol targeting, trades
def test_signal_rules() -> None:
    p = pd.Series([0.40, 0.47, 0.50, 0.53, 0.70, np.nan])  # short band: p < 1 - 0.52 = 0.48
    assert signals_from_predictions(p, "proba", 0.52, False).tolist()[:5] == [0, 0, 0, 1, 1]
    assert signals_from_predictions(p, "proba", 0.52, True).tolist()[:5] == [-1, -1, 0, 1, 1]
    assert np.isnan(signals_from_predictions(p, "proba", 0.52, True).iloc[-1])
    r = pd.Series([-0.01, 0.0, 0.002])
    assert signals_from_predictions(r, "return", 0.001, True).tolist() == [-1, 0, 1]


def test_volatility_targeting_uses_only_past_volatility() -> None:
    prices = _prices(200)
    vol_full = realised_volatility(prices, 30)
    vol_cut = realised_volatility(prices.iloc[:120], 30)
    assert vol_full.iloc[119] == vol_cut.iloc[119]  # truncation-invariant

    cfg = ZERO_COST.replace(vol_target=0.2, max_leverage=1.0)
    daily = run_backtest(_ones(prices, 40, -5), prices, cfg).daily
    assert (daily["position"] <= 1.0).all() and (daily["position"] > 0).any()
    expected = (0.2 / vol_full).clip(upper=1.0).shift(1).reindex(daily.index)
    np.testing.assert_allclose(daily["position"].iloc[1:], expected.iloc[1:])


def test_trades_compound_to_the_strategy_return() -> None:
    prices = _prices()
    rng = np.random.default_rng(3)
    target = pd.Series(rng.integers(0, 2, 200).astype(float), index=prices.index[10:210])
    result = run_backtest(target, prices, BacktestConfig())
    in_trades = np.prod(1 + result.trades["return"])
    assert in_trades == pytest.approx(result.daily["equity_net"].iloc[-1], rel=1e-10)
    assert (result.trades["days"] >= 1).all() and set(result.trades["direction"]) == {"long"}


def test_empty_signal_rejected() -> None:
    with pytest.raises(ValueError, match="no signals"):
        run_backtest(pd.Series(dtype=float), _prices(10), ZERO_COST)


# ---------------------------------------------------------------- required: drawdown by hand
def test_max_drawdown_on_hand_computed_series() -> None:
    # equity: 1 → 1.2 → 0.9 → 1.0 → 1.3 → 0.65
    returns = pd.Series([0.2, -0.25, 1.0 / 9, 0.3, -0.5])
    dd = drawdown_stats(returns)
    assert dd["max_drawdown"] == pytest.approx(-0.5)  # 1.3 → 0.65
    assert dd["max_drawdown_peak"] == 4 and dd["max_drawdown_trough"] == 5
    # under water from the 1.2 peak (idx 1) until 1.3 (idx 4) = 3 days; then 1 open day
    assert dd["max_drawdown_duration_days"] == 3
    assert dd["in_drawdown_at_end"]


def test_losing_from_day_one_is_a_drawdown() -> None:
    dd = drawdown_stats(pd.Series([-0.1, -0.1]))
    assert dd["max_drawdown"] == pytest.approx(0.81 - 1) and dd["max_drawdown_duration_days"] == 2


def test_performance_metrics_by_hand() -> None:
    idx = pd.date_range("2024-01-01", periods=365, freq="1D", tz="UTC")
    r = pd.Series(np.tile([0.01, -0.005], 183)[:365], index=idx)
    m = performance_metrics(r, position=pd.Series(1.0, index=idx))
    assert m["cagr"] == pytest.approx((1 + r).prod() - 1)  # exactly one year
    assert m["volatility"] == pytest.approx(r.std(ddof=1) * np.sqrt(365))
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(365))
    downside = np.sqrt(np.mean(np.minimum(r, 0) ** 2))
    assert m["sortino"] == pytest.approx(r.mean() / downside * np.sqrt(365))
    assert m["exposure"] == 1.0 and m["hit_rate"] == pytest.approx((r > 0).mean())


def test_yearly_returns() -> None:
    idx = pd.date_range("2023-12-30", periods=4, freq="1D", tz="UTC")
    y = yearly_returns(pd.Series([0.1, 0.1, -0.5, 0.0], index=idx))
    assert y == {"2023": pytest.approx(0.21), "2024": pytest.approx(-0.5)}
