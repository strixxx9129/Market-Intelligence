"""Look-ahead bias tests for every registered feature.

TRUNCATION TEST: if a feature only uses the past, its value at day t cannot depend on whether
the days after t exist. So we compute it twice: on the full series, and on the series cut at
day t. The value at t must be IDENTICAL (exactly: a prefix runs the same arithmetic in the same
order). Any feature peeking at t+1, t+2, ... changes when the future is removed and fails.

Contributors get this check for free: it is parametrised over the registry.
"""

import inspect
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from marketlens.features import FEATURES
from marketlens.features.registry import FeatureSpec

N_DAYS = 400


@pytest.fixture(scope="module")
def candles() -> pd.DataFrame:
    """Deterministic synthetic daily candles: random walk, a +60% jump, zero-volume days."""
    rng = np.random.default_rng(7)
    log_ret = rng.standard_t(df=3, size=N_DAYS) * 0.03
    log_ret[250] = np.log(1.6)
    close = 1000 * np.exp(np.cumsum(log_ret))
    open_ = np.concatenate([[1000.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.random(N_DAYS) * 0.03)
    low = np.minimum(open_, close) * (1 - rng.random(N_DAYS) * 0.03)
    volume = rng.lognormal(mean=8, sigma=1, size=N_DAYS)
    volume[[120, 121, 122]] = 0.0  # like Bitstamp, Jan 2015
    index = pd.date_range("2020-01-01", periods=N_DAYS, freq="1D", tz="UTC", name="ts")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume}, index=index
    )


def _cut_points(spec: FeatureSpec) -> list[int]:
    """Edges (around the warm-up boundary, the jump, zero volume, the end) plus a spread."""
    edges = {spec.lookback, spec.lookback + 1, 121, 249, 250, 251, N_DAYS - 2, N_DAYS - 1}
    spread = set(np.linspace(spec.lookback, N_DAYS - 1, 17).astype(int))
    return sorted(i for i in edges | spread if 0 <= i < N_DAYS)


def _assert_truncation_invariant(spec: FeatureSpec, candles: pd.DataFrame) -> None:
    full = spec.compute(candles)
    for i in _cut_points(spec):
        t = candles.index[i]
        truncated = spec.compute(candles.iloc[: i + 1])
        a, b = full.loc[t], truncated.loc[t]
        assert (np.isnan(a) and np.isnan(b)) or a == b, (
            f"{spec.name} at {t.date()}: {a!r} on full series vs {b!r} when cut at that day. "
            "The feature uses data after day t (look-ahead bias)."
        )


@pytest.mark.parametrize("name", sorted(FEATURES))
def test_truncation_invariance(name: str, candles: pd.DataFrame) -> None:
    _assert_truncation_invariant(FEATURES[name], candles)


@pytest.mark.parametrize("name", sorted(FEATURES))
def test_lookback_is_declared_honestly(name: str, candles: pd.DataFrame) -> None:
    """NaN exactly for the first `lookback` rows: not too short (would fake warm-up values),
    not too long (would waste history and make the builder drop too few rows)."""
    spec = FEATURES[name]
    values = spec.compute(candles)
    assert values.iloc[: spec.lookback].isna().all(), f"{name}: values before lookback"
    assert np.isfinite(values.iloc[spec.lookback :]).all(), f"{name}: NaN/inf after lookback"


@pytest.mark.parametrize("name", sorted(FEATURES))
def test_feature_is_documented(name: str) -> None:
    spec = FEATURES[name]
    assert len(spec.description) >= 10
    assert spec.family and spec.name == name


# ---------------------------------------------------------------- negative controls
# These deliberately leaky features are NOT registered; they prove the test above can fail.
def _centred_sma(c: pd.DataFrame) -> pd.Series:
    # WRONG: a centred window at day t averages days t-2 .. t+2, two days of the future.
    return c["close"].rolling(5, center=True).mean() / c["close"] - 1


def _tomorrow_return(c: pd.DataFrame) -> pd.Series:
    # WRONG: shift(-1) pulls tomorrow's close into today's row. This is the target, not a feature.
    return np.log(c["close"].shift(-1) / c["close"])


def _global_zscore(c: pd.DataFrame) -> pd.Series:
    # WRONG (subtle): mean/std over the WHOLE series include future prices.
    return (c["close"] - c["close"].mean()) / c["close"].std()


@pytest.mark.parametrize("leaky", [_centred_sma, _tomorrow_return, _global_zscore])
def test_truncation_test_catches_leaky_features(leaky: object, candles: pd.DataFrame) -> None:
    spec = FeatureSpec(
        name=leaky.__name__,
        description="leaky on purpose",
        lookback=0,  # type: ignore[attr-defined]
        family="negative_control",
        func=leaky,
    )  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="look-ahead"):
        _assert_truncation_invariant(spec, candles)


# ---------------------------------------------------------------- static guard
# Every module that defines a registered feature, including ones contributors add later.
FEATURE_MODULES = sorted({Path(inspect.getsourcefile(s.func) or "") for s in FEATURES.values()})
FORBIDDEN = ("shift(-", "center=True", "import targets", "features.targets", "bfill", "backfill")


@pytest.mark.parametrize(
    "path",
    FEATURE_MODULES,
    ids=lambda p: p.name,
)
def test_feature_modules_contain_no_forward_looking_code(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    hits = [token for token in FORBIDDEN if token in source]
    assert not hits, f"{path.name} contains forward-looking code: {hits}"
