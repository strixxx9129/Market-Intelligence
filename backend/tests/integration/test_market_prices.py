from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import Asset, MarketPrice

TS = datetime(2024, 1, 1, tzinfo=UTC)


async def _asset(session: AsyncSession) -> Asset:
    asset = Asset(
        symbol="TEST-USD",
        name="Test coin",
        asset_class="crypto",
        base_currency="TEST",
        quote_currency="USD",
    )
    session.add(asset)
    await session.flush()
    return asset


def _candle(asset_id: int, ts: datetime = TS, interval: str = "1d") -> MarketPrice:
    return MarketPrice(
        asset_id=asset_id,
        interval=interval,
        ts=ts,
        open=Decimal("42000.12345678"),
        high=Decimal("43000"),
        low=Decimal("41000"),
        close=Decimal("42500.5"),
        volume=Decimal("1234.5"),
        source="test",
    )


async def test_unique_constraint_rejects_duplicate_candle(db_session: AsyncSession) -> None:
    asset = await _asset(db_session)
    db_session.add(_candle(asset.id))
    await db_session.flush()

    db_session.add(_candle(asset.id))
    with pytest.raises(IntegrityError, match="uq_market_prices_asset_id_interval_ts"):
        await db_session.flush()


async def test_same_ts_allowed_for_different_interval_or_day(db_session: AsyncSession) -> None:
    asset = await _asset(db_session)
    db_session.add_all(
        [
            _candle(asset.id),
            _candle(asset.id, interval="1h"),
            _candle(asset.id, ts=TS + timedelta(days=1)),
        ]
    )
    await db_session.flush()

    count = await db_session.scalar(
        select(func.count()).select_from(MarketPrice).where(MarketPrice.asset_id == asset.id)
    )
    assert count == 3


async def test_prices_round_trip_exactly_and_ts_is_utc(db_session: AsyncSession) -> None:
    asset = await _asset(db_session)
    db_session.add(_candle(asset.id))
    await db_session.flush()
    db_session.expunge_all()

    row = await db_session.scalar(select(MarketPrice).where(MarketPrice.asset_id == asset.id))

    assert row is not None
    assert row.open == Decimal("42000.12345678")  # NUMERIC keeps every decimal digit
    assert row.ts == TS
    assert row.ts.utcoffset() == timedelta(0)


async def test_check_constraint_rejects_high_below_low(db_session: AsyncSession) -> None:
    asset = await _asset(db_session)
    bad = _candle(asset.id)
    bad.high, bad.low = Decimal("1"), Decimal("2")
    db_session.add(bad)

    with pytest.raises(IntegrityError, match="ck_market_prices_high_gte_low"):
        await db_session.flush()
