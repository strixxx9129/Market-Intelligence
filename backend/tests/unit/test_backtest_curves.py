"""Weekly equity-curve downsampling for the website."""

import numpy as np
import pandas as pd

from marketlens.backtest.curves import downsample_curve


def _curve(days: int) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=days, freq="1D", tz="UTC")  # a Monday
    growth = np.cumprod(np.full(days, 1.001))
    return pd.DataFrame(
        {
            "equity_gross": growth * 1.01,
            "equity_net": growth,
            "equity_buy_and_hold": growth * 0.99,
            "equity_baseline": growth,
            "position": 1.0,  # ignored
        },
        index=idx,
    )


def test_one_point_per_week_ending_on_the_last_day() -> None:
    curve = _curve(100)
    out = downsample_curve(curve)
    assert out["frequency"] == "weekly"
    assert len(out["ts"]) == 15  # 14 full weeks + the final (partial-week) day
    assert out["ts"][-1] == curve.index[-1].isoformat()
    assert out["strategy_net"][-1] == round(float(curve["equity_net"].iloc[-1]), 6)
    # Weekly value = the level on the week's last day (Sunday), not an average.
    assert out["ts"][0] == "2024-01-07T00:00:00+00:00"
    assert out["strategy_net"][0] == round(float(curve["equity_net"].iloc[6]), 6)
    assert set(out) == {
        "frequency",
        "ts",
        "strategy_gross",
        "strategy_net",
        "buy_and_hold",
        "baseline",
    }


def test_missing_values_become_null_and_empty_input_is_fine() -> None:
    curve = _curve(14)
    curve.loc[curve.index[:7], "equity_baseline"] = np.nan
    out = downsample_curve(curve)
    assert out["baseline"][0] is None and out["baseline"][-1] is not None
    assert downsample_curve(curve.iloc[:0])["ts"] == []
