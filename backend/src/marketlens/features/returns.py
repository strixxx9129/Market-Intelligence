"""Past log returns.

Possible signal: short-term reversal or momentum. Phase 2 found daily return autocorrelation
is weak and fading (Ljung-Box p 0.000 → 0.094 across eras), so expect little on their own;
they matter mostly as inputs that interact with volatility.
"""

import numpy as np
import pandas as pd

from marketlens.features.registry import register_feature


@register_feature("log_return_1d", "Log return over the last day: ln(close_t / close_t-1)", 1)
def log_return_1d(c: pd.DataFrame) -> pd.Series:
    return np.log(c["close"] / c["close"].shift(1))


@register_feature("log_return_7d", "Log return over the last 7 days: ln(close_t / close_t-7)", 7)
def log_return_7d(c: pd.DataFrame) -> pd.Series:
    return np.log(c["close"] / c["close"].shift(7))


@register_feature(
    "log_return_30d", "Log return over the last 30 days: ln(close_t / close_t-30)", 30
)
def log_return_30d(c: pd.DataFrame) -> pd.Series:
    return np.log(c["close"] / c["close"].shift(30))
