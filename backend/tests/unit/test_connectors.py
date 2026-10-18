from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import pandas as pd
import pytest

from marketlens.ingestion.connectors import base
from marketlens.ingestion.connectors.base import (
    BaseConnector,
    available_connectors,
    get_connector,
    register_connector,
)
from marketlens.ingestion.connectors.kaggle_btc import KaggleBtcConnector
from marketlens.ingestion.types import FetchResult, empty_candles
from tests.fixtures.kaggle_btc_fixture import EXPECTED_ERROR_CHECKS, FIXTURE_PATH, write_fixture


class _DummyConnector(BaseConnector):
    name: ClassVar[str] = "dummy_test"
    supported_intervals: ClassVar[tuple[str, ...]] = ("1d",)

    def fetch(
        self, asset: str, start: datetime | None, end: datetime | None, interval: str = "1d"
    ) -> FetchResult:
        return FetchResult(empty_candles(), [], 0)


# ---------------------------------------------------------------- registry
def test_builtin_connector_is_registered() -> None:
    assert "kaggle_btc" in available_connectors()
    assert isinstance(get_connector("kaggle_btc"), KaggleBtcConnector)


def test_unknown_connector_lists_available() -> None:
    with pytest.raises(KeyError, match="kaggle_btc"):
        get_connector("does_not_exist")


def test_name_collision_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "_REGISTRY", dict(base._REGISTRY))

    class Impostor(_DummyConnector):
        name: ClassVar[str] = "kaggle_btc"

    with pytest.raises(ValueError, match="already registered"):
        register_connector(Impostor)


def test_entry_point_plugins_are_discovered(monkeypatch: pytest.MonkeyPatch) -> None:
    """A third-party package only declares an entry point; MarketLens finds it."""

    class FakeEntryPoint:
        def __init__(self, name: str, obj: Any) -> None:
            self.name, self._obj = name, obj

        def load(self) -> Any:
            return self._obj

    def broken() -> Any:
        raise ImportError("plugin dependency missing")

    class BrokenEntryPoint(FakeEntryPoint):
        def load(self) -> Any:
            return broken()

    monkeypatch.setattr(base, "_REGISTRY", dict(base._REGISTRY))
    monkeypatch.setattr(base, "_plugins_loaded", False)
    monkeypatch.setattr(
        base,
        "entry_points",
        lambda group: [
            FakeEntryPoint("dummy_test", _DummyConnector),
            BrokenEntryPoint("broken", None),  # must not break the others
            FakeEntryPoint("not_a_connector", object),  # ignored
        ],
    )

    assert "dummy_test" in available_connectors()
    assert "broken" not in available_connectors()


def test_unsupported_asset_or_interval_rejected() -> None:
    connector = KaggleBtcConnector(FIXTURE_PATH)
    with pytest.raises(ValueError, match="asset"):
        connector.check_supports("ETH-USD", "1d")
    with pytest.raises(ValueError, match="interval"):
        connector.check_supports("BTC-USD", "1h")


# ---------------------------------------------------------------- kaggle_btc connector
@pytest.fixture(scope="module")
def fetched() -> FetchResult:
    return KaggleBtcConnector(FIXTURE_PATH).fetch("BTC-USD", None, None)


def _clean_minutes() -> pd.DataFrame:
    """Independent reference: parse the fixture naively, keep only well-formed minutes."""
    raw = pd.read_csv(FIXTURE_PATH, dtype=str, on_bad_lines="skip")
    num = raw.apply(pd.to_numeric, errors="coerce").dropna()
    num = num[num["Timestamp"] % 60 == 0]
    num = num[~num["Timestamp"].duplicated(keep="first")]
    ok = (
        (num["Low"] <= num[["Open", "Close"]].min(axis=1))
        & (num["High"] >= num[["Open", "Close"]].max(axis=1))
        & (num["Volume"] >= 0)
    )
    num = num[ok]
    num.index = pd.to_datetime(num["Timestamp"].astype("int64"), unit="s", utc=True)
    return num


def test_daily_candles_match_reference_resample(fetched: FetchResult) -> None:
    minutes = _clean_minutes()
    ref = minutes.resample("1D").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    ref = ref.iloc[:3]  # day 4 is incomplete and must not be emitted
    ref.index = ref.index.as_unit("ns")  # pandas 3 infers second resolution from unit="s"

    candles = fetched.candles.set_index("ts")
    assert list(candles.index) == list(ref.index)
    for ours, theirs in [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")]:
        pd.testing.assert_series_equal(
            candles[ours], ref[theirs], check_names=False, check_freq=False
        )
    pd.testing.assert_series_equal(
        candles["volume"], ref["Volume"], check_names=False, check_freq=False, rtol=1e-12
    )


def test_minute_counts_and_zero_volume_fills(fetched: FetchResult) -> None:
    c = fetched.candles
    # day 1: 30 zero-volume fills; day 2: 4 broken minutes replaced → 1436 usable minutes
    assert c["minutes_present"].tolist() == [1440, 1436, 1440]
    assert c["minutes_traded"].tolist() == [1410, 1436, 1440]


def test_every_broken_row_is_reported_not_dropped(fetched: FetchResult) -> None:
    errors = {i.check_name for i in fetched.issues if i.severity.value == "error"}
    assert errors == EXPECTED_ERROR_CHECKS
    # 3 days + 120 minutes + 1 duplicate + 1 garbled-timestamp line + 1 malformed line
    assert fetched.rows_read == 3 * 1440 + 120 + 3


def test_incomplete_last_day_is_withheld_and_reported(fetched: FetchResult) -> None:
    assert fetched.candles["ts"].iloc[-1] == pd.Timestamp("2024-01-03", tz="UTC")
    incomplete = [i for i in fetched.issues if i.check_name == "incomplete_period"]
    assert len(incomplete) == 1
    assert incomplete[0].details["minutes_present"] == 120


def test_chunk_boundaries_do_not_change_results(fetched: FetchResult) -> None:
    """Days split across chunks are merged from partial aggregates; results must be identical."""
    small = KaggleBtcConnector(FIXTURE_PATH, chunksize=333).fetch("BTC-USD", None, None)
    pd.testing.assert_frame_equal(small.candles, fetched.candles)
    assert small.rows_read == fetched.rows_read


def test_start_filters_older_days_and_their_issues() -> None:
    result = KaggleBtcConnector(FIXTURE_PATH).fetch(
        "BTC-USD", datetime(2024, 1, 3, tzinfo=UTC), None
    )
    assert result.candles["ts"].tolist() == [pd.Timestamp("2024-01-03", tz="UTC")]
    dated_errors = [i for i in result.issues if i.ts is not None and i.severity.value == "error"]
    assert dated_errors == []  # 2024-01-02 problems were reported by the earlier run


def test_header_change_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("time,open,high,low,close,vol\n1,1,1,1,1,1\n")
    with pytest.raises(ValueError, match="unexpected CSV header"):
        KaggleBtcConnector(bad).fetch("BTC-USD", None, None)


def test_missing_file_explains_how_to_fix(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="make data"):
        KaggleBtcConnector(tmp_path / "nope.csv").fingerprint()


def test_fingerprint_changes_with_content(tmp_path: Path) -> None:
    a = KaggleBtcConnector(FIXTURE_PATH).fingerprint()
    longer = KaggleBtcConnector(write_fixture(tmp_path / "x.csv", days=4)).fingerprint()
    assert a.sha256 != longer.sha256
    assert a.bytes == FIXTURE_PATH.stat().st_size
