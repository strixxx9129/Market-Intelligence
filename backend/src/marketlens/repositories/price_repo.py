"""SQL for `market_prices`. No business logic here: callers pass already-validated candles.

UPSERT = INSERT ... ON CONFLICT (asset_id, interval, ts) DO UPDATE. Re-running a load leaves
the table identical (idempotency). The `WHERE ... IS DISTINCT FROM` clause skips rows whose
values did not change, so an unchanged re-run rewrites nothing and the RETURNING count
reports only rows that were really inserted or changed.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import MarketPrice

CHUNK_SIZE = 5_000
_VALUE_COLUMNS = ("open", "high", "low", "close", "volume")


def to_decimal(value: float) -> Decimal:
    """float → exact NUMERIC(…, 8). Going through an 8-decimal string avoids binary-float
    artifacts such as Decimal(0.1) == 0.1000000000000000055511151231257827…"""
    return Decimal(f"{value:.8f}")


def candles_to_rows(
    candles: pd.DataFrame, asset_id: int, interval: str, source: str
) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": asset_id,
            "interval": interval,
            "ts": row.ts.to_pydatetime(),
            "open": to_decimal(row.open),
            "high": to_decimal(row.high),
            "low": to_decimal(row.low),
            "close": to_decimal(row.close),
            "volume": to_decimal(row.volume),
            "source": source,
        }
        for row in candles.itertuples(index=False)
    ]


async def upsert_prices(
    session: AsyncSession, rows: Sequence[dict[str, Any]], chunk_size: int = CHUNK_SIZE
) -> int:
    """UPSERT rows in chunks; returns how many rows were inserted or actually changed.

    Each chunk is sent as one executemany(); SQLAlchemy's "insertmanyvalues" batches it into
    multi-row INSERTs that stay under the driver's bind-parameter limit (32,767 for asyncpg).
    """
    base = insert(MarketPrice)
    excluded = base.excluded
    stmt = base.on_conflict_do_update(
        constraint="uq_market_prices_asset_id_interval_ts",
        set_={
            **{c: excluded[c] for c in (*_VALUE_COLUMNS, "source")},
            "ingested_at": func.now(),
        },
        where=or_(
            *(getattr(MarketPrice, c).is_distinct_from(excluded[c]) for c in _VALUE_COLUMNS),
            MarketPrice.source.is_distinct_from(excluded.source),
        ),
    ).returning(MarketPrice.id)

    changed = 0
    for i in range(0, len(rows), chunk_size):
        result = await session.execute(stmt, list(rows[i : i + chunk_size]))
        changed += len(result.all())
    return changed


async def last_candle(
    session: AsyncSession, asset_id: int, interval: str
) -> tuple[datetime, Decimal] | None:
    """(ts, close) of the most recent stored candle, used by incremental mode."""
    row = (
        await session.execute(
            select(MarketPrice.ts, MarketPrice.close)
            .where(MarketPrice.asset_id == asset_id, MarketPrice.interval == interval)
            .order_by(MarketPrice.ts.desc())
            .limit(1)
        )
    ).first()
    return (row.ts, row.close) if row else None


async def count_prices(session: AsyncSession, asset_id: int, interval: str) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(MarketPrice)
        .where(MarketPrice.asset_id == asset_id, MarketPrice.interval == interval)
    )
    return int(count or 0)
