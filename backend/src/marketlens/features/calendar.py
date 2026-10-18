"""Calendar effects. Bitcoin trades 7 days a week, so weekends are real trading days.

Possible signal: weekend liquidity is thinner (fewer institutions, closed traditional markets)
and some weekly patterns have been reported. Known in advance, so trivially leakage-free.
Encoded as an integer; models that need it categorical should one-hot it in their pipeline.
"""

import pandas as pd

from marketlens.features.registry import register_feature


@register_feature("day_of_week", "UTC day of week of the candle: 0 = Monday ... 6 = Sunday", 0)
def day_of_week(c: pd.DataFrame) -> pd.Series:
    return pd.Series(c.index.dayofweek, index=c.index, dtype="float64")
