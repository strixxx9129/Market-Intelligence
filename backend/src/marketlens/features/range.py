"""Intraday range.

Possible signal: (high - low) / close is a one-day volatility estimate that also sees moves
which reverted before the close (the close-to-close return misses them). Range-based
estimators are known to be more efficient than close-to-close ones.
"""

import pandas as pd

from marketlens.features.registry import register_feature


@register_feature("range_pct", "(high - low) / close of the day", 0)
def range_pct(c: pd.DataFrame) -> pd.Series:
    return (c["high"] - c["low"]) / c["close"]
