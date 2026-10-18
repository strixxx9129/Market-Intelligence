"""Trading activity.

Possible signal: unusual volume accompanies news, liquidations and regime changes, and often
precedes or confirms large moves. Volume is heavy-tailed and contains zero-volume days
(Phase 3 found 3 in Jan 2015), so we work on log(1 + volume), which is finite at zero.
"""

import numpy as np
import pandas as pd

from marketlens.features.registry import register_feature


@register_feature(
    "volume_zscore_30d",
    "z-score of log(1+volume) today versus the PREVIOUS 30 days (today excluded)",
    30,
)
def volume_zscore_30d(c: pd.DataFrame) -> pd.Series:
    lv = np.log1p(c["volume"])
    past = lv.shift(1).rolling(30)
    return (lv - past.mean()) / past.std()


@register_feature("volume_change_1d", "log(1+volume_t) - log(1+volume_t-1)", 1)
def volume_change_1d(c: pd.DataFrame) -> pd.Series:
    return np.log1p(c["volume"]).diff()
