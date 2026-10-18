from decimal import Decimal

import pandas as pd
import pytest

from marketlens.ingestion.validation import validate_candles
from marketlens.repositories.price_repo import to_decimal


def _candles(**overrides: list[float | int]) -> pd.DataFrame:
    n = len(next(iter(overrides.values()))) if overrides else 4
    base: dict[str, list[float | int]] = {
        "open": [100.0 + i for i in range(n)],
        "high": [110.0 + i for i in range(n)],
        "low": [90.0 + i for i in range(n)],
        "close": [101.0 + i for i in range(n)],
        "volume": [5.0] * n,
        "minutes_present": [1440] * n,
        "minutes_traded": [1440] * n,
    }
    base.update(overrides)
    frame = pd.DataFrame(base)
    frame.insert(0, "ts", pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC").as_unit("ns"))
    return frame


def _checks(result_issues: list, severity: str) -> dict[str, list[str]]:  # type: ignore[type-arg]
    out: dict[str, list[str]] = {}
    for issue in result_issues:
        if issue.severity.value == severity:
            out.setdefault(issue.check_name, []).append(issue.ts.date().isoformat())
    return out


def test_clean_candles_pass_untouched() -> None:
    candles = _candles()
    result = validate_candles(candles)
    assert result.quarantined == 0
    assert result.issues == []
    pd.testing.assert_frame_equal(result.valid, candles)


@pytest.mark.parametrize(
    ("overrides", "check"),
    [
        ({"low": [90.0, -1.0, 92.0, 93.0]}, "non_positive_price"),
        ({"volume": [5.0, -2.0, 5.0, 5.0]}, "negative_volume"),
        ({"high": [110.0, 95.0, 112.0, 113.0]}, "ohlc_inconsistent"),  # high < close
        ({"minutes_traded": [1440, 1500, 1440, 1440]}, "schema:minutes_traded:in_range(0, 1440)"),
    ],
)
def test_errors_are_quarantined_with_their_values(
    overrides: dict[str, list[float | int]], check: str
) -> None:
    result = validate_candles(_candles(**overrides))

    assert result.quarantined == 1
    assert len(result.valid) == 3
    assert pd.Timestamp("2024-01-02", tz="UTC") not in set(result.valid["ts"])
    errors = [i for i in result.issues if i.severity.value == "error"]
    assert check in {i.check_name for i in errors}
    assert all(i.details["action"] == "quarantined" and "row" in i.details for i in errors)


def test_null_price_is_quarantined() -> None:
    result = validate_candles(_candles(close=[101.0, float("nan"), 103.0, 104.0]))
    assert result.quarantined == 1
    assert "null_value" in _checks(result.issues, "error")


def test_duplicate_timestamp_keeps_first() -> None:
    candles = _candles()
    candles.loc[2, "ts"] = candles.loc[1, "ts"]
    result = validate_candles(candles)
    assert result.quarantined == 1
    assert _checks(result.issues, "error") == {"duplicate_timestamp": ["2024-01-02"]}


def test_warnings_are_loaded_but_reported() -> None:
    candles = _candles(
        close=[101.0, 160.0, 102.0, 103.0],  # +58% then -36%
        high=[110.0, 170.0, 112.0, 113.0],
        volume=[5.0, 5.0, 0.0, 5.0],
        minutes_traded=[1440, 1440, 0, 30],
    )
    result = validate_candles(candles)

    assert result.quarantined == 0 and len(result.valid) == 4
    warnings = _checks(result.issues, "warning")
    assert warnings["extreme_move"] == ["2024-01-02"]
    assert warnings["zero_volume"] == ["2024-01-03"]
    assert warnings["low_coverage"] == ["2024-01-03", "2024-01-04"]


def test_previous_close_checks_first_new_day() -> None:
    result = validate_candles(_candles(), previous_close=50.0)  # 50 → 101 = +102%
    assert _checks(result.issues, "warning") == {"extreme_move": ["2024-01-01"]}


def test_missing_day_reported_but_quarantined_day_is_not_missing() -> None:
    candles = _candles(volume=[5.0, -1.0, 5.0, 5.0]).drop(index=3)
    gap_day = candles.iloc[-1]["ts"] + pd.Timedelta(days=2)
    extra = candles.iloc[[-1]].assign(ts=gap_day)
    result = validate_candles(pd.concat([candles, extra], ignore_index=True))

    warnings = _checks(result.issues, "warning")
    assert warnings["missing_period"] == ["2024-01-04"]  # the real gap only
    assert "negative_volume" in _checks(result.issues, "error")  # 01-02: quarantined, not "missing"


def test_structural_problem_fails_the_run() -> None:
    with pytest.raises(ValueError, match="schema"):
        validate_candles(_candles().drop(columns="volume"))


def test_to_decimal_is_exact_to_8_places() -> None:
    assert to_decimal(0.1 + 0.2) == Decimal("0.30000000")
    assert to_decimal(42_000.123456789) == Decimal("42000.12345679")
