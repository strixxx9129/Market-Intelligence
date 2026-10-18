"""Value objects shared by connectors, validation and the pipeline."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from marketlens.models.data_quality_issue import IssueSeverity

# Canonical candle frame produced by every connector (one row per period, ts = period open, UTC).
CANDLE_COLUMNS = [
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "minutes_present",
    "minutes_traded",
]
PRICE_COLUMNS = ["open", "high", "low", "close"]


@dataclass(frozen=True)
class QualityIssue:
    """One data-quality finding. `error` rows are quarantined; `warning` rows are still loaded."""

    check_name: str
    severity: IssueSeverity
    interval: str
    ts: datetime | None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceFingerprint:
    """Identity of a raw input: where it came from and a hash of its exact content."""

    uri: str
    sha256: str
    bytes: int


@dataclass
class FetchResult:
    candles: pd.DataFrame  # columns: CANDLE_COLUMNS
    issues: list[QualityIssue]
    rows_read: int  # raw records read from the source (before any filtering)


def empty_candles() -> pd.DataFrame:
    frame = pd.DataFrame({c: pd.Series(dtype="float64") for c in CANDLE_COLUMNS})
    frame["ts"] = pd.Series(dtype="datetime64[ns, UTC]")
    frame[["minutes_present", "minutes_traded"]] = frame[
        ["minutes_present", "minutes_traded"]
    ].astype("int64")
    return frame
