"""Realised volatility (std of daily log returns; not annualised).

Possible signal: the strongest structure Phase 2 found is volatility clustering (ACF of
|returns| 0.33 at lag 1, significant in every era). Recent volatility predicts the SIZE of
tomorrow's move, and the 7/30 ratio says whether the market is calming down or heating up
(a regime-change indicator).
"""

import numpy as np
import pandas as pd

from marketlens.features.registry import register_feature


def _daily_log_returns(c: pd.DataFrame) -> pd.Series:
    return np.log(c["close"] / c["close"].shift(1))


@register_feature("volatility_7d", "Std of daily log returns over the last 7 days", 7)
def volatility_7d(c: pd.DataFrame) -> pd.Series:
    return _daily_log_returns(c).rolling(7).std()


@register_feature("volatility_30d", "Std of daily log returns over the last 30 days", 30)
def volatility_30d(c: pd.DataFrame) -> pd.Series:
    return _daily_log_returns(c).rolling(30).std()


@register_feature(
    "volatility_ratio_7_30",
    "volatility_7d / volatility_30d: >1 means volatility is rising versus the last month",
    30,
)
def volatility_ratio_7_30(c: pd.DataFrame) -> pd.Series:
    r = _daily_log_returns(c)
    return r.rolling(7).std() / r.rolling(30).std()
