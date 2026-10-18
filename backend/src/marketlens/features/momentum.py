"""Distance of the close from its trailing simple moving average (trend / momentum).

Possible signal: trend-following effects (prices above their average tend to keep trending)
and mean reversion after stretched moves. Expressed as a ratio, so it is scale-free and
comparable between $10 and $100,000 prices (Phase 2: never feed raw price levels).
"""

import pandas as pd

from marketlens.features.registry import register_feature


@register_feature("close_to_sma_20", "close / SMA(close, 20 days incl. today) - 1", 19)
def close_to_sma_20(c: pd.DataFrame) -> pd.Series:
    return c["close"] / c["close"].rolling(20).mean() - 1


@register_feature("close_to_sma_50", "close / SMA(close, 50 days incl. today) - 1", 49)
def close_to_sma_50(c: pd.DataFrame) -> pd.Series:
    return c["close"] / c["close"].rolling(50).mean() - 1
