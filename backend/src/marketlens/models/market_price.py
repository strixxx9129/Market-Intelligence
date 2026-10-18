"""OHLCV candles.

Design notes:
- NUMERIC, not float: floats cannot represent most decimals exactly (0.1 + 0.2 != 0.3), and
  prices must round-trip without drift. Convert to float only inside feature/ML code.
- `ts` is the candle OPEN time in UTC (timestamptz). A daily candle with ts=2024-01-01 covers
  2024-01-01T00:00Z..2024-01-02T00:00Z and is only fully known after it closes; feature code
  must respect that to avoid look-ahead bias.
- UNIQUE(asset_id, interval, ts) is the natural key that ingestion UPSERTs target, which makes
  re-running an ingestion job idempotent.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base

PRICE = Numeric(20, 8)
VOLUME = Numeric(30, 8)
SUPPORTED_INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d", "1w")


class MarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = (
        UniqueConstraint("asset_id", "interval", "ts"),
        CheckConstraint(
            "interval IN (" + ", ".join(f"'{i}'" for i in SUPPORTED_INTERVALS) + ")",
            name="interval_valid",
        ),
        CheckConstraint("high >= low", name="high_gte_low"),
        CheckConstraint(
            "open >= 0 AND high >= 0 AND low >= 0 AND close >= 0 AND volume >= 0",
            name="non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    high: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    low: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    close: Mapped[Decimal] = mapped_column(PRICE, nullable=False)
    volume: Mapped[Decimal] = mapped_column(VOLUME, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
