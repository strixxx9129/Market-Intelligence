"""Connector for the Kaggle dataset "mczielinski/bitcoin-historical-data" (Bitstamp BTC/USD,
1-minute OHLCV, CC BY-SA 4.0).

It only READS the local file downloaded by `make data`; it never touches the network, so
ingestion is repeatable offline and the API never depends on Kaggle.

How it works (memory stays at ~one chunk, not 7.8M rows):
1. Read the CSV in chunks as *strings*, so one garbled value can't crash the whole parse.
2. Clean each chunk: every unusable minute becomes a QualityIssue (nothing is dropped silently).
3. Reduce each chunk to small per-day partial aggregates (first/last ts, open, close, max high,
   min low, summed volume, minute counts).
4. Merge the partials. A day split across two chunks still gets the right open/close because
   partials are ordered by their first timestamp.
5. Drop the last day if it is still forming (no look-ahead) and report it as a warning.
"""

import hashlib
import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from marketlens.core.config import get_settings
from marketlens.ingestion.connectors.base import BaseConnector, register_connector
from marketlens.ingestion.kaggle_download import download_dataset
from marketlens.ingestion.types import (
    CANDLE_COLUMNS,
    FetchResult,
    QualityIssue,
    SourceFingerprint,
    empty_candles,
)
from marketlens.models.data_quality_issue import IssueSeverity

DATASET = "mczielinski/bitcoin-historical-data"
FILE_NAME = "btcusd_1-min_data.csv"
RAW_COLUMNS = ["Timestamp", "Open", "High", "Low", "Close", "Volume"]
VALUE_COLUMNS = ["open", "high", "low", "close", "volume"]
SECONDS_PER_DAY = 86_400
MAX_EXAMPLES = 20  # cap on raw examples stored for issues that have no timestamp
_BAD_LINE = re.compile(r"line (\d+): expected (\d+) fields, saw (\d+)")


@register_connector
class KaggleBtcConnector(BaseConnector):
    name = "kaggle_btc"
    supported_intervals = ("1d",)
    supported_assets = ("BTC-USD",)

    def __init__(self, csv_path: Path | str | None = None, chunksize: int = 1_000_000) -> None:
        self.csv_path = Path(csv_path) if csv_path else get_settings().raw_data_dir / FILE_NAME
        self.chunksize = chunksize

    def refresh(self) -> dict[str, Any] | None:
        """Download the latest dataset version from Kaggle into the raw directory."""
        result = download_dataset(DATASET, self.csv_path.parent)
        return {
            "dataset": result.dataset,
            "licences": result.licences,
            "downloaded": result.downloaded,
            "changed_files": result.changed,
        }

    # ------------------------------------------------------------------ fingerprint
    def fingerprint(self) -> SourceFingerprint:
        self._require_file()
        digest = hashlib.sha256()
        with self.csv_path.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
        return SourceFingerprint(
            uri=str(self.csv_path), sha256=digest.hexdigest(), bytes=self.csv_path.stat().st_size
        )

    # ------------------------------------------------------------------ fetch
    def fetch(
        self, asset: str, start: datetime | None, end: datetime | None, interval: str = "1d"
    ) -> FetchResult:
        self.check_supports(asset, interval)
        self._require_file()
        start_s = int(start.timestamp()) if start else None
        end_s = int(end.timestamp()) if end else None

        issues: list[QualityIssue] = []
        undated: dict[str, list[dict[str, Any]]] = {}  # check_name -> raw examples
        undated_counts: dict[str, int] = {}
        partials: list[pd.DataFrame] = []
        rows_read = 0
        running_max: float = -np.inf  # highest valid timestamp seen so far (sorted-file contract)

        self._check_header()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", pd.errors.ParserWarning)
            # No `usecols`: with it, pandas silently discards extra fields, so a malformed
            # 7-field line would be parsed as a normal row. Without it, the line is reported
            # as a ParserWarning, which we turn into a `malformed_line` issue.
            reader = pd.read_csv(
                self.csv_path,
                dtype=str,
                chunksize=self.chunksize,
                on_bad_lines="warn",
            )
            for chunk in reader:
                rows_read += len(chunk)
                clean, running_max = self._clean_chunk(
                    chunk, running_max, start_s, end_s, issues, undated, undated_counts
                )
                if not clean.empty:
                    partials.append(_daily_partials(clean))

        for w in caught:
            for line, expected, saw in _BAD_LINE.findall(str(w.message)):
                rows_read += 1
                _add_undated(
                    undated,
                    undated_counts,
                    "malformed_line",
                    {"line": int(line), "expected_fields": int(expected), "fields": int(saw)},
                )

        for check, examples in undated.items():
            issues.append(
                QualityIssue(
                    check_name=check,
                    severity=IssueSeverity.ERROR,
                    interval="1m",
                    ts=None,
                    details={"count": undated_counts[check], "examples": examples},
                )
            )

        candles = _merge_partials(partials)
        candles, incomplete = _drop_incomplete_last_day(candles)
        if incomplete is not None:
            issues.append(incomplete)
        return FetchResult(candles=candles, issues=issues, rows_read=rows_read)

    # ------------------------------------------------------------------ helpers
    def _require_file(self) -> None:
        if not self.csv_path.exists():
            raise FileNotFoundError(f"{self.csv_path} not found; download it with `make data`")

    def _check_header(self) -> None:
        """A different header means the upstream format changed: fail the run, loudly."""
        with self.csv_path.open(encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
        if header != RAW_COLUMNS:
            raise ValueError(f"unexpected CSV header {header}; expected {RAW_COLUMNS}")

    def _clean_chunk(
        self,
        chunk: pd.DataFrame,
        running_max: float,
        start_s: int | None,
        end_s: int | None,
        issues: list[QualityIssue],
        undated: dict[str, list[dict[str, Any]]],
        undated_counts: dict[str, int],
    ) -> tuple[pd.DataFrame, float]:
        raw = chunk.rename(columns=str.lower)
        ts = pd.to_numeric(raw["timestamp"], errors="coerce")

        # 1. Timestamp must be a whole-minute number; otherwise the row cannot be placed in time.
        bad_ts = ts.isna() | (ts % 60 != 0)
        for _, row in raw[bad_ts].iterrows():
            _add_undated(undated, undated_counts, "invalid_timestamp", {"raw": _raw(row)})
        raw, ts = raw[~bad_ts], ts[~bad_ts].astype("int64")

        # 2. Duplicates / out-of-order: the source is sorted ascending, so any timestamp that is
        #    not greater than every earlier one is a repeat. The first occurrence wins.
        prev_max = ts.cummax().shift(1).fillna(running_max)
        prev_max = np.maximum(prev_max, running_max)
        dup = ts <= prev_max
        new_running_max = float(max(running_max, ts.max())) if len(ts) else running_max

        # 3. Only the requested window matters from here on (incremental mode).
        in_window = pd.Series(True, index=ts.index)
        if start_s is not None:
            in_window &= ts >= start_s
        if end_s is not None:
            in_window &= ts < end_s
        raw, ts, dup = raw[in_window], ts[in_window], dup[in_window]

        values = raw[VALUE_COLUMNS].apply(pd.to_numeric, errors="coerce")
        missing = values.isna() & raw[VALUE_COLUMNS].isna()
        garbled = values.isna() & raw[VALUE_COLUMNS].notna()
        complete = ~values.isna().any(axis=1)
        prices = values[["open", "high", "low", "close"]]
        body_lo = prices[["open", "close"]].min(axis=1)
        body_hi = prices[["open", "close"]].max(axis=1)

        checks: list[tuple[str, pd.Series]] = [
            ("duplicate_timestamp", dup),
            ("missing_value", missing.any(axis=1)),
            ("garbled_value", garbled.any(axis=1)),
            ("non_positive_price", complete & (prices <= 0).any(axis=1)),
            ("negative_volume", complete & (values["volume"] < 0)),
            (
                "ohlc_inconsistent",
                complete
                & (
                    (prices["low"] > body_lo)
                    | (prices["high"] < body_hi)
                    | (prices["low"] > prices["high"])
                ),
            ),
        ]
        bad = pd.Series(False, index=ts.index)
        for check, mask in checks:
            # One issue per (minute, check): a minute can fail several checks.
            for idx in mask[mask].index:
                issues.append(
                    QualityIssue(
                        check_name=check,
                        severity=IssueSeverity.ERROR,
                        interval="1m",
                        ts=pd.Timestamp(int(ts[idx]), unit="s", tz="UTC").to_pydatetime(),
                        details={"raw": _raw(raw.loc[idx])},
                    )
                )
            bad |= mask

        clean = values[~bad].assign(ts=ts[~bad])
        return clean, new_running_max


def _raw(row: pd.Series) -> dict[str, str | None]:
    return {str(k): (None if pd.isna(v) else str(v)) for k, v in row.items()}


def _add_undated(
    undated: dict[str, list[dict[str, Any]]],
    counts: dict[str, int],
    check: str,
    example: dict[str, Any],
) -> None:
    counts[check] = counts.get(check, 0) + 1
    examples = undated.setdefault(check, [])
    if len(examples) < MAX_EXAMPLES:
        examples.append(example)


def _daily_partials(clean: pd.DataFrame) -> pd.DataFrame:
    clean = clean.sort_values("ts", kind="stable")
    day = (clean["ts"] // SECONDS_PER_DAY) * SECONDS_PER_DAY
    g = clean.groupby(day, sort=True)
    return pd.DataFrame(
        {
            "first_ts": g["ts"].min(),
            "last_ts": g["ts"].max(),
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "minutes_present": g.size(),
            "minutes_traded": (clean["volume"] > 0).groupby(day).sum(),
        }
    ).rename_axis("day")


def _merge_partials(partials: list[pd.DataFrame]) -> pd.DataFrame:
    if not partials:
        return empty_candles()
    parts = pd.concat(partials).reset_index().sort_values(["day", "first_ts"], kind="stable")
    g = parts.groupby("day", sort=True)
    daily = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "minutes_present": g["minutes_present"].sum().astype("int64"),
            "minutes_traded": g["minutes_traded"].sum().astype("int64"),
            "last_ts": g["last_ts"].max(),
        }
    ).reset_index()
    daily["ts"] = pd.to_datetime(daily["day"], unit="s", utc=True).dt.as_unit("ns")
    return daily[[*CANDLE_COLUMNS, "last_ts"]]


def _drop_incomplete_last_day(candles: pd.DataFrame) -> tuple[pd.DataFrame, QualityIssue | None]:
    """The final day is only complete if its last minute (23:59 UTC) has been observed."""
    if candles.empty:
        return candles.drop(columns="last_ts", errors="ignore"), None
    last = candles.iloc[-1]
    day_start = int(last["ts"].timestamp())
    last_minute = day_start + SECONDS_PER_DAY - 60
    out = candles.drop(columns="last_ts")
    if int(last["last_ts"]) >= last_minute:
        return out, None
    issue = QualityIssue(
        check_name="incomplete_period",
        severity=IssueSeverity.WARNING,
        interval="1d",
        ts=last["ts"].to_pydatetime(),
        details={
            "minutes_present": int(last["minutes_present"]),
            "last_minute_seen": pd.Timestamp(int(last["last_ts"]), unit="s", tz="UTC").isoformat(),
            "action": "not loaded yet; will be loaded once the day is complete",
        },
    )
    return out.iloc[:-1].reset_index(drop=True), issue
