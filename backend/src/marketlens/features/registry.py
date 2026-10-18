"""Feature registry: the plugin point for contributors.

A feature is one small, pure function `f(candles) -> pd.Series`:

    @register_feature("close_to_sma_20", "Close relative to its 20-day mean", lookback=19)
    def close_to_sma_20(c: pd.DataFrame) -> pd.Series:
        return c["close"] / c["close"].rolling(20).mean() - 1

Contract (enforced by tests for EVERY registered feature):
- Input: daily candles indexed by UTC `ts` (period open), ascending, with float columns
  open/high/low/close/volume. Row t's close is known at the END of day t.
- The value at row t may use rows <= t only. Never `shift(-k)`, centred windows, or full-series
  statistics (global mean/std, min-max scaling); the truncation test catches these.
- `lookback` = how many earlier rows the feature needs: the value is NaN for the first
  `lookback` rows and defined from row `lookback` on.
"""

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

FeatureFunc = Callable[[pd.DataFrame], pd.Series]
CANDLE_FIELDS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    description: str
    lookback: int
    family: str
    func: FeatureFunc

    def compute(self, candles: pd.DataFrame) -> pd.Series:
        result = self.func(candles)
        if not isinstance(result, pd.Series) or not result.index.equals(candles.index):
            raise TypeError(f"feature {self.name!r} must return a Series aligned to the input")
        return result.astype("float64").rename(self.name)


FEATURES: dict[str, FeatureSpec] = {}


def register_feature(
    name: str, description: str, lookback: int, *, family: str | None = None
) -> Callable[[FeatureFunc], FeatureFunc]:
    if lookback < 0:
        raise ValueError("lookback must be >= 0")

    def decorator(func: FeatureFunc) -> FeatureFunc:
        existing = FEATURES.get(name)
        if existing is not None and existing.func is not func:
            raise ValueError(f"feature {name!r} is already registered by {existing.func!r}")
        FEATURES[name] = FeatureSpec(
            name=name,
            description=description,
            lookback=lookback,
            family=family or func.__module__.rsplit(".", 1)[-1],
            func=func,
        )
        return func

    return decorator


def check_candles(candles: pd.DataFrame) -> None:
    """Fail fast if the input violates the feature contract."""
    missing = set(CANDLE_FIELDS) - set(candles.columns)
    if missing:
        raise ValueError(f"candles missing columns: {sorted(missing)}")
    if not isinstance(candles.index, pd.DatetimeIndex) or candles.index.tz is None:
        raise ValueError("candles must be indexed by a timezone-aware DatetimeIndex")
    if not candles.index.is_monotonic_increasing or candles.index.has_duplicates:
        raise ValueError("candles index must be strictly increasing")
