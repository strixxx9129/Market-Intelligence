"""Compact equity curves for the website.

The full daily curve stays in object storage (Parquet). The API may only read Postgres/Redis
(rule 5), so we also store a WEEKLY downsample in `backtests.equity_curve`: ~52 points a year,
enough to draw the shape, small enough for one JSONB value. Weekly = the last value of each week
(equity is a level, so "last" is exact at that date; averaging would invent values).
"""

from typing import Any

import pandas as pd

SERIES = {
    "strategy_gross": "equity_gross",
    "strategy_net": "equity_net",
    "buy_and_hold": "equity_buy_and_hold",
    "baseline": "equity_baseline",
}


def downsample_curve(curve: pd.DataFrame, rule: str = "W-SUN") -> dict[str, Any]:
    """{"frequency", "ts": [...], "<series>": [...]}: the last day of each week, so the
    final point is always the final day."""
    frame = curve[[c for c in SERIES.values() if c in curve.columns]].sort_index()
    # tail(1) keeps each week's real last row (and its real date). resample().last() would
    # label a partial final week with the NEXT Sunday, a date after the data ends.
    weekly = frame.groupby(pd.Grouper(freq=rule)).tail(1)
    out: dict[str, Any] = {
        "frequency": "weekly",
        "ts": [ts.isoformat() for ts in weekly.index],
    }
    for name, column in SERIES.items():
        if column in weekly.columns:
            out[name] = [None if pd.isna(v) else round(float(v), 6) for v in weekly[column]]
    return out
