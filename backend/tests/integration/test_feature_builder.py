from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.db.seed import seed_assets
from marketlens.features import builder as builder_module
from marketlens.features.builder import FeatureBuilder, FeatureSetChangedError
from marketlens.features.targets import next_day_log_return
from marketlens.models import Feature, FeatureSet, PipelineRun, PipelineRunStatus
from marketlens.repositories import feature_repo, ingestion_repo, price_repo

Sessionmaker = async_sessionmaker[AsyncSession]
N_DAYS = 120


def _rows(asset_id: int, n: int, start: str = "2024-01-01") -> list[dict[str, object]]:
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    vol = rng.lognormal(5, 1, n)
    days = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return [
        {
            "asset_id": asset_id,
            "interval": "1d",
            "ts": d.to_pydatetime(),
            "open": price_repo.to_decimal(c),
            "high": price_repo.to_decimal(c * 1.02),
            "low": price_repo.to_decimal(c * 0.98),
            "close": price_repo.to_decimal(c),
            "volume": price_repo.to_decimal(v),
            "source": "test",
        }
        for d, c, v in zip(days, close, vol, strict=True)
    ]


@pytest.fixture
async def asset_with_prices(db_sessionmaker: Sessionmaker) -> int:
    async with db_sessionmaker() as s, s.begin():
        await seed_assets(s)
        asset_id = await ingestion_repo.get_asset_id(s, "BTC-USD")
        assert asset_id is not None
        await price_repo.upsert_prices(s, _rows(asset_id, N_DAYS))
    return asset_id


async def _feature_rows(sm: Sessionmaker) -> list[tuple[object, dict[str, float]]]:
    async with sm() as s:
        rows = await s.execute(select(Feature.ts, Feature.values).order_by(Feature.ts))
        return [(r.ts, r.values) for r in rows]


async def test_build_drops_warmup_and_stores_all_features(
    db_sessionmaker: Sessionmaker, asset_with_prices: int
) -> None:
    result = await FeatureBuilder(db_sessionmaker).build("v1")

    assert result.candles == N_DAYS and result.warmup_rows == 49
    assert result.rows == N_DAYS - 49 and result.rows_changed == N_DAYS - 49
    rows = await _feature_rows(db_sessionmaker)
    assert rows[0][0] == pd.Timestamp("2024-01-01", tz="UTC") + pd.Timedelta(days=49)
    assert set(rows[0][1]) == set(builder_module.FEATURE_SETS["v1"])
    assert all(np.isfinite(list(values.values())).all() for _, values in rows)
    # Targets live elsewhere: nothing about the future is stored with the features.
    assert not any("next_day" in k for _, values in rows for k in values)

    async with db_sessionmaker() as s:
        fs = await feature_repo.get_feature_set(s, "v1")
        run = await s.get(PipelineRun, result.run_id)
    assert fs is not None and fs.code_hash == result.code_hash
    assert fs.definition["warmup_rows"] == 49 and len(fs.definition["features"]) == 12
    assert run is not None and run.status is PipelineRunStatus.SUCCESS


async def test_stored_values_match_direct_computation(
    db_sessionmaker: Sessionmaker, asset_with_prices: int
) -> None:
    await FeatureBuilder(db_sessionmaker).build("v1")
    async with db_sessionmaker() as s:
        candles = await price_repo.load_candles(s, asset_with_prices, "1d")
        fs = await feature_repo.get_feature_set(s, "v1")
        assert fs is not None
        stored = await feature_repo.load_features(s, asset_with_prices, fs.id)

    expected = builder_module.drop_warmup(
        builder_module.compute_features(candles, builder_module.resolve_set("v1")), 49
    )
    pd.testing.assert_frame_equal(
        stored[expected.columns], expected, check_names=False, check_freq=False, rtol=1e-12
    )
    # A model would pair row t with the target of t → t+1, computed separately:
    target = next_day_log_return(candles).reindex(stored.index)
    assert np.isnan(target.iloc[-1])  # the last day's future is unknown


async def test_rebuild_is_idempotent(db_sessionmaker: Sessionmaker, asset_with_prices: int) -> None:
    await FeatureBuilder(db_sessionmaker).build("v1")
    before = await _feature_rows(db_sessionmaker)

    again = await FeatureBuilder(db_sessionmaker).build("v1")

    assert again.rows_changed == 0
    assert await _feature_rows(db_sessionmaker) == before
    async with db_sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(FeatureSet)) == 1


async def test_new_candle_adds_exactly_one_feature_row(
    db_sessionmaker: Sessionmaker, asset_with_prices: int
) -> None:
    await FeatureBuilder(db_sessionmaker).build("v1")
    before = await _feature_rows(db_sessionmaker)

    async with db_sessionmaker() as s, s.begin():
        extra = _rows(asset_with_prices, N_DAYS + 1)[-1]
        await price_repo.upsert_prices(
            s, [{**extra, "close": Decimal("150"), "high": Decimal("155")}]
        )
    result = await FeatureBuilder(db_sessionmaker).build("v1")

    assert result.rows_changed == 1  # history is untouched: past-only features can't change
    after = await _feature_rows(db_sessionmaker)
    assert after[:-1] == before and len(after) == len(before) + 1


async def test_changed_feature_code_requires_new_version(
    db_sessionmaker: Sessionmaker, asset_with_prices: int
) -> None:
    await FeatureBuilder(db_sessionmaker).build("v1")
    async with db_sessionmaker() as s, s.begin():  # simulate: v1 was built by other code
        await s.execute(update(FeatureSet).values(code_hash="0" * 64))

    with pytest.raises(FeatureSetChangedError, match="immutable"):
        await FeatureBuilder(db_sessionmaker).build("v1")

    # (Not "latest by started_at": inside one test transaction now() is constant.)
    async with db_sessionmaker() as s:
        failed = (
            await s.scalars(
                select(PipelineRun).where(PipelineRun.status == PipelineRunStatus.FAILED)
            )
        ).all()
    assert len(failed) == 1 and "immutable" in (failed[0].error or "")


async def test_too_little_history_fails_loudly(db_sessionmaker: Sessionmaker) -> None:
    async with db_sessionmaker() as s, s.begin():
        await seed_assets(s)
        asset_id = await ingestion_repo.get_asset_id(s, "BTC-USD")
        assert asset_id is not None
        await price_repo.upsert_prices(s, _rows(asset_id, 10))
    result = await FeatureBuilder(db_sessionmaker).build("v1")
    assert result.rows == 0  # 10 days < 49 warm-up rows: nothing to store, nothing invented
