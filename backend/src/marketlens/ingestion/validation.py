"""Validation of canonical daily candles before they are loaded.

Two kinds of checks:

- ERRORS (Pandera schema): the row is wrong and must not reach `market_prices`.
  Non-positive or null prices, negative volume, OHLC inconsistency, duplicate timestamps,
  impossible minute counts. The row is *quarantined*: excluded from the load and stored in
  `data_quality_issues` with its values.
- WARNINGS: the row is plausible but unusual. Zero volume, low traded-minute coverage,
  |return| > 50%, missing days. The row IS loaded and the issue is recorded, so a human (or
  monitoring) can review it later.

Invariant: every input row ends up either in `valid` or in exactly one-or-more error issues.
Nothing disappears silently.
"""

from dataclasses import dataclass
from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaErrors

from marketlens.ingestion.types import PRICE_COLUMNS, QualityIssue
from marketlens.models.data_quality_issue import IssueSeverity

EXTREME_MOVE = 0.50  # |simple return| above this is flagged (real moves exist: -48.5% in 2013)
MIN_TRADED_MINUTES = 60  # less than one hour of actual trading in a day

_price = pa.Column(float, checks=pa.Check.gt(0), nullable=False)

CANDLE_SCHEMA = pa.DataFrameSchema(
    {
        "ts": pa.Column("datetime64[ns, UTC]", unique=True, report_duplicates="exclude_first"),
        "open": _price,
        "high": _price,
        "low": _price,
        "close": _price,
        "volume": pa.Column(float, checks=pa.Check.ge(0), nullable=False),
        "minutes_present": pa.Column("int64", checks=pa.Check.in_range(0, 1440)),
        "minutes_traded": pa.Column("int64", checks=pa.Check.in_range(0, 1440)),
    },
    checks=[
        pa.Check(
            lambda df: (
                (df["low"] <= df[["open", "close"]].min(axis=1))
                & (df["high"] >= df[["open", "close"]].max(axis=1))
                & (df["low"] <= df["high"])
            ),
            name="ohlc_consistent",  # low <= open, close <= high
        ),
        pa.Check(
            lambda df: df["minutes_traded"] <= df["minutes_present"],
            name="traded_le_present",
        ),
    ],
    strict="filter",  # extra columns are ignored, not an error
    coerce=False,
)

# Pandera (column, check) → our stable check_name, used as part of the issue's natural key.
_CHECK_NAMES: dict[tuple[str | None, str], str] = {
    ("ts", "field_uniqueness"): "duplicate_timestamp",
    ("volume", "greater_than_or_equal_to(0)"): "negative_volume",
    (None, "ohlc_consistent"): "ohlc_inconsistent",
    (None, "traded_le_present"): "minutes_inconsistent",
}


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    issues: list[QualityIssue]
    quarantined: int


def validate_candles(
    candles: pd.DataFrame, interval: str = "1d", previous_close: float | None = None
) -> ValidationResult:
    """Split candles into loadable rows and issues.

    `previous_close` is the last stored close before the first candle (incremental mode),
    so the first new day's return can be checked too.
    """
    candles = candles.sort_values("ts", kind="stable").reset_index(drop=True)
    if candles.empty:
        return ValidationResult(valid=candles, issues=[], quarantined=0)

    errors = _schema_errors(candles)
    issues: list[QualityIssue] = []
    bad_index: set[int] = set()
    for idx, check_names in errors.items():
        bad_index.add(idx)
        row = candles.loc[idx]
        for check in sorted(check_names):
            issues.append(
                QualityIssue(
                    check_name=check,
                    severity=IssueSeverity.ERROR,
                    interval=interval,
                    ts=_ts(row["ts"]),
                    details={"row": _row_details(row), "action": "quarantined"},
                )
            )

    valid = candles.drop(index=sorted(bad_index)).reset_index(drop=True)
    issues.extend(_warnings(valid, interval, previous_close))
    issues.extend(_gaps(candles, interval))
    if len(valid) + len(bad_index) != len(candles):  # nothing may disappear silently
        raise RuntimeError("validation lost rows: valid + quarantined != input")
    return ValidationResult(valid=valid, issues=issues, quarantined=len(bad_index))


def _schema_errors(candles: pd.DataFrame) -> dict[int, set[str]]:
    """Run the Pandera schema lazily (collect all failures) and map them to row indices."""
    try:
        CANDLE_SCHEMA.validate(candles, lazy=True)
    except SchemaErrors as exc:
        cases = exc.failure_cases
    else:
        return {}

    per_row: dict[int, set[str]] = {}
    for case in cases.itertuples(index=False):
        if pd.isna(case.index):
            # Structural problem (missing column, wrong dtype): not a row issue → fail the run.
            raise ValueError(f"candle frame does not match schema: {case.check} on {case.column}")
        column = None if case.schema_context == "DataFrameSchema" else str(case.column)
        check = str(case.check)
        name = _CHECK_NAMES.get((column, check))
        if name is None:
            if column in PRICE_COLUMNS and check.startswith("greater_than"):
                name = "non_positive_price"
            elif check == "not_nullable":
                name = "null_value"
            else:
                name = f"schema:{column or 'frame'}:{check}"
        per_row.setdefault(int(case.index), set()).add(name)
    return per_row


def _warnings(
    valid: pd.DataFrame, interval: str, previous_close: float | None
) -> list[QualityIssue]:
    issues: list[QualityIssue] = []

    def warn(check: str, ts: Any, details: dict[str, Any]) -> None:
        issues.append(QualityIssue(check, IssueSeverity.WARNING, interval, _ts(ts), details))

    for row in valid[valid["volume"] == 0].itertuples():
        warn("zero_volume", row.ts, {"close": row.close})

    for row in valid[valid["minutes_traded"] < MIN_TRADED_MINUTES].itertuples():
        warn("low_coverage", row.ts, {"minutes_traded": int(row.minutes_traded)})

    prev = valid["close"].shift(1)
    if previous_close is not None:
        prev.iloc[0] = previous_close
    ret = valid["close"] / prev - 1
    for i in ret[ret.abs() > EXTREME_MOVE].index:
        warn(
            "extreme_move",
            valid.at[i, "ts"],
            {
                "return": round(float(ret[i]), 6),
                "close": float(valid.at[i, "close"]),
                "previous_close": float(prev[i]),
            },
        )

    return issues


def _gaps(candles: pd.DataFrame, interval: str) -> list[QualityIssue]:
    """Days absent from the source. Uses ALL input rows, so a quarantined day is not ALSO
    reported as missing."""
    if interval != "1d" or len(candles) < 2:
        return []
    expected = pd.date_range(candles["ts"].iloc[0], candles["ts"].iloc[-1], freq="1D")
    return [
        QualityIssue(
            "missing_period",
            IssueSeverity.WARNING,
            interval,
            _ts(day),
            {"note": "no candle for this day in the source"},
        )
        for day in expected.difference(pd.DatetimeIndex(candles["ts"]))
    ]


def _ts(value: Any) -> Any:
    return pd.Timestamp(value).to_pydatetime()


def _row_details(row: pd.Series) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key == "ts":
            continue
        out[str(key)] = None if pd.isna(value) else float(value)
    return out
