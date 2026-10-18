import numpy as np
import pandas as pd
import pytest

from marketlens.features import FEATURES, register_feature
from marketlens.features import registry as registry_module
from marketlens.features.builder import (
    code_hash,
    compute_features,
    drop_warmup,
    resolve_set,
    set_definition,
    warmup_rows,
)
from marketlens.features.registry import FeatureSpec, check_candles
from marketlens.features.targets import next_day_log_return, next_day_up


def _candles(close: list[float], volume: list[float] | None = None) -> pd.DataFrame:
    n = len(close)
    c = np.array(close, dtype=float)
    return pd.DataFrame(
        {
            "open": c,
            "high": c * 1.1,
            "low": c * 0.9,
            "close": c,
            "volume": np.array(volume if volume is not None else [100.0] * n, dtype=float),
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC", name="ts"),
    )


def _f(name: str, candles: pd.DataFrame) -> pd.Series:
    return FEATURES[name].compute(candles)


# ---------------------------------------------------------------- hand-calculated values
def test_log_returns() -> None:
    c = _candles([100.0, 110.0, 99.0] + [99.0] * 30)
    assert _f("log_return_1d", c).iloc[1] == pytest.approx(np.log(1.1))
    assert _f("log_return_1d", c).iloc[2] == pytest.approx(np.log(0.9))
    assert _f("log_return_7d", c).iloc[7] == pytest.approx(np.log(99 / 100))
    assert _f("log_return_30d", c).iloc[30] == pytest.approx(np.log(99 / 100))


def test_volatility_and_ratio() -> None:
    prices = 100 * np.exp(np.cumsum([0.0] + [0.01, -0.01] * 20))
    c = _candles(list(prices))
    r = np.log(c["close"]).diff()
    assert _f("volatility_7d", c).iloc[10] == pytest.approx(r.iloc[4:11].std())
    assert _f("volatility_30d", c).iloc[35] == pytest.approx(r.iloc[6:36].std())
    ratio = _f("volatility_ratio_7_30", c).iloc[35]
    assert ratio == pytest.approx(r.iloc[29:36].std() / r.iloc[6:36].std())


def test_momentum() -> None:
    c = _candles([100.0] * 19 + [120.0] + [100.0] * 30)
    expected = 120 / ((19 * 100 + 120) / 20) - 1
    assert _f("close_to_sma_20", c).iloc[19] == pytest.approx(expected)
    flat = _candles([50.0] * 60)
    assert _f("close_to_sma_50", flat).iloc[59] == pytest.approx(0.0)


def test_range_pct() -> None:
    c = _candles([100.0, 200.0])
    np.testing.assert_allclose(_f("range_pct", c), [0.2, 0.2])


def test_volume_features_survive_zero_volume() -> None:
    vols = [100.0 + i for i in range(31)] + [0.0, 0.0, 100.0]
    c = _candles([100.0] * len(vols), vols)
    change = _f("volume_change_1d", c)
    assert np.isfinite(change.iloc[1:]).all()  # log1p: no -inf on a zero-volume day
    assert change.iloc[31] == pytest.approx(np.log1p(0) - np.log1p(130.0))
    z = _f("volume_zscore_30d", c)
    past = np.log1p(np.array(vols[0:30]))
    assert z.iloc[30] == pytest.approx((np.log1p(vols[30]) - past.mean()) / past.std(ddof=1))
    assert z.iloc[31] < -5  # a zero-volume day is extremely unusual


def test_day_of_week() -> None:
    c = _candles([1.0] * 7)  # 2024-01-01 was a Monday
    assert _f("day_of_week", c).tolist() == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0]


# ---------------------------------------------------------------- targets
def test_targets_look_one_day_ahead_and_end_with_nan() -> None:
    c = _candles([100.0, 110.0, 110.0, 99.0])
    ret = next_day_log_return(c)
    assert ret.iloc[0] == pytest.approx(np.log(1.1))  # row t = return from t to t+1
    assert np.isnan(ret.iloc[-1])  # tomorrow is unknown
    assert next_day_up(c).tolist()[:3] == [1.0, 0.0, 0.0]  # flat counts as "not up"
    assert np.isnan(next_day_up(c).iloc[-1])


# ---------------------------------------------------------------- registry
def test_registry_rejects_duplicate_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "FEATURES", dict(FEATURES))
    with pytest.raises(ValueError, match="already registered"):

        @registry_module.register_feature("range_pct", "duplicate name", 0)
        def other(c: pd.DataFrame) -> pd.Series:
            return c["close"]


def test_register_feature_infers_family(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry_module, "FEATURES", {})

    @registry_module.register_feature("my_feature", "a contributor's feature", 3)
    def my_feature(c: pd.DataFrame) -> pd.Series:
        return c["close"]

    spec = registry_module.FEATURES["my_feature"]
    assert spec.lookback == 3 and spec.family == "test_features"
    assert register_feature is registry_module.register_feature


def test_feature_must_return_aligned_series() -> None:
    bad = FeatureSpec("bad", "returns wrong length", 0, "x", lambda c: c["close"].iloc[:-1])
    with pytest.raises(TypeError, match="aligned"):
        bad.compute(_candles([1.0, 2.0, 3.0]))


def test_candle_contract_is_checked() -> None:
    c = _candles([1.0, 2.0])
    with pytest.raises(ValueError, match="missing"):
        check_candles(c.drop(columns="volume"))
    with pytest.raises(ValueError, match="increasing"):
        check_candles(c.iloc[::-1])
    with pytest.raises(ValueError, match="timezone"):
        check_candles(c.tz_localize(None))


# ---------------------------------------------------------------- builder pure functions
def test_v1_set_resolves_and_warmup_is_max_lookback() -> None:
    specs = resolve_set("v1")
    assert len(specs) == 12
    assert warmup_rows(specs) == 49
    with pytest.raises(KeyError, match="unknown feature set"):
        resolve_set("v999")


def test_drop_warmup_removes_rows_and_rejects_leftover_nan() -> None:
    specs = resolve_set("v1")
    c = _candles(
        list(100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.02, 120)))),
        list(np.random.default_rng(2).lognormal(5, 1, 120)),
    )
    frame = compute_features(c, specs)
    out = drop_warmup(frame, warmup_rows(specs))
    assert len(out) == 120 - 49 and out.index[0] == c.index[49]
    with pytest.raises(ValueError, match="non-finite"):
        drop_warmup(frame, 10)  # too few rows dropped → NaN left → refuse


def test_code_hash_changes_when_definition_changes() -> None:
    specs = resolve_set("v1")
    d = set_definition("v1", specs)
    assert code_hash(d, specs) == code_hash(d, specs)
    assert code_hash({**d, "version": "v2"}, specs) != code_hash(d, specs)
    assert code_hash(set_definition("v1", specs[:-1]), specs[:-1]) != code_hash(d, specs)
