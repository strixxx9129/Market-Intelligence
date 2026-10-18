"""Deterministic generator for Kaggle-format BTC minute CSVs used in tests.

`kaggle_btc_sample.csv` (committed) = write_fixture(path) with the defaults:
3 complete days (2024-01-01..03) + 120 minutes of 2024-01-04 (an incomplete day).

Deliberately broken content (all on 2024-01-02 unless noted):
    minute 100  Close is empty                       → missing_value
    minute 200  High below Low                       → ohlc_inconsistent
    minute 300  negative Volume                      → negative_volume
    minute 400  repeated with different values       → duplicate_timestamp (the 2nd copy)
    minute 500  Open = "abc"                         → garbled_value
    after 600   a line whose Timestamp is "not-a-ts" → invalid_timestamp
    after 700   a line with 7 fields instead of 6    → malformed_line
    2024-01-01 minutes 600-629: zero-volume flat fills (valid rows, no trades)
    2024-01-03 onward: prices x1.6 → day-3 return ≈ +60% → extreme_move warning

Extending `days` keeps every earlier row identical (same seed, sequential draws), so a longer
file is a strict superset: exactly what incremental mode sees after a new download.
Regenerate the committed file with:  python tests/fixtures/kaggle_btc_fixture.py
"""

from datetime import UTC, datetime
from pathlib import Path

import numpy as np

FIXTURE_PATH = Path(__file__).with_name("kaggle_btc_sample.csv")
START = datetime(2024, 1, 1, tzinfo=UTC)
HEADER = "Timestamp,Open,High,Low,Close,Volume"

BROKEN_DAY = 1  # 0-based day index → 2024-01-02
EXTREME_DAY = 2  # 2024-01-03
EXPECTED_ERROR_CHECKS = {
    "missing_value",
    "ohlc_inconsistent",
    "negative_volume",
    "duplicate_timestamp",
    "garbled_value",
    "invalid_timestamp",
    "malformed_line",
}


def write_fixture(path: Path = FIXTURE_PATH, days: int = 3, partial_minutes: int = 120) -> Path:
    rng = np.random.default_rng(42)
    total = days * 1440 + partial_minutes
    t0 = int(START.timestamp())
    price = 42_000.0
    lines = [HEADER]

    for i in range(total):
        day, minute = divmod(i, 1440)
        # Draw the same number of random values for every minute so extending `days`
        # never changes earlier rows.
        step, wick_hi, wick_lo, vol = rng.normal(0, 8), rng.random(), rng.random(), rng.random()
        open_ = price
        price = max(1.0, price + step)
        close = price
        scale = 1.6 if day >= EXTREME_DAY else 1.0
        o, c = open_ * scale, close * scale
        h, low = max(o, c) + wick_hi * 5, min(o, c) - wick_lo * 5
        v = round(0.01 + vol * 2, 8)
        ts = t0 + i * 60

        if day == 0 and 600 <= minute < 630:  # zero-volume flat fill
            o = h = low = c = round(o, 2)
            v = 0.0
        row = [str(ts), f"{o:.2f}", f"{h:.2f}", f"{low:.2f}", f"{c:.2f}", f"{v:.8f}"]

        if day == BROKEN_DAY:
            if minute == 100:
                row[4] = ""
            elif minute == 200:
                row[2] = f"{low - 1:.2f}"
            elif minute == 300:
                row[5] = "-1.5"
            elif minute == 500:
                row[1] = "abc"
        lines.append(",".join(row))

        if day == BROKEN_DAY:
            if minute == 400:
                lines.append(",".join([str(ts), "1.00", "2.00", "0.50", "1.50", "9.0"]))
            elif minute == 600:
                lines.append("not-a-ts,1,2,0.5,1.5,1")
            elif minute == 700:
                lines.append(f"{ts},1,2,0.5,1.5,1,EXTRA")

    path.write_text("\n".join(lines) + "\n")
    return path


if __name__ == "__main__":
    print(f"wrote {write_fixture()}")
