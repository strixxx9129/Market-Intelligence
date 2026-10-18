from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.db.seed import seed_assets
from marketlens.ingestion.connectors.kaggle_btc import KaggleBtcConnector
from marketlens.ingestion.pipeline import IngestionPipeline, IngestMode
from marketlens.ingestion.types import FetchResult
from marketlens.models import (
    Asset,
    DataQualityIssue,
    IssueSeverity,
    MarketPrice,
    PipelineRun,
    PipelineRunStatus,
    RawIngestion,
)
from tests.fixtures.kaggle_btc_fixture import EXPECTED_ERROR_CHECKS, FIXTURE_PATH, write_fixture

Sessionmaker = async_sessionmaker[AsyncSession]


@pytest.fixture
async def seeded(db_sessionmaker: Sessionmaker) -> Sessionmaker:
    async with db_sessionmaker() as s, s.begin():
        await seed_assets(s)
    return db_sessionmaker


def _pipeline(sm: Sessionmaker, path: Path = FIXTURE_PATH) -> IngestionPipeline:
    return IngestionPipeline(sm, KaggleBtcConnector(path), "BTC-USD")


async def _count(sm: Sessionmaker, model: type) -> int:
    async with sm() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


PriceRow = tuple[datetime, object, object, object, object, object]


async def _prices(sm: Sessionmaker) -> list[PriceRow]:
    async with sm() as s:
        rows = await s.execute(
            select(
                MarketPrice.ts,
                MarketPrice.open,
                MarketPrice.high,
                MarketPrice.low,
                MarketPrice.close,
                MarketPrice.volume,
            )
            .join(Asset)
            .where(Asset.symbol == "BTC-USD")
            .order_by(MarketPrice.ts)
        )
        return [tuple(r) for r in rows]


async def test_full_ingest_loads_valid_candles_and_quarantines_broken_rows(
    seeded: Sessionmaker,
) -> None:
    result = await _pipeline(seeded).run(IngestMode.FULL)

    assert result.status is PipelineRunStatus.SUCCESS
    assert result.candles_valid == 3 and result.rows_changed == 3
    prices = await _prices(seeded)
    assert [p[0].date().isoformat() for p in prices] == ["2024-01-01", "2024-01-02", "2024-01-03"]

    async with seeded() as s:
        issues = (await s.scalars(select(DataQualityIssue))).all()
        run = await s.get(PipelineRun, result.run_id)
    errors = {i.check_name for i in issues if i.severity is IssueSeverity.ERROR}
    warnings = {i.check_name for i in issues if i.severity is IssueSeverity.WARNING}
    assert errors == EXPECTED_ERROR_CHECKS
    assert warnings == {"extreme_move", "incomplete_period"}
    # Quarantined rows keep their raw values for later inspection.
    garbled = next(i for i in issues if i.check_name == "garbled_value")
    assert garbled.details["raw"]["open"] == "abc"

    assert run is not None and run.status is PipelineRunStatus.SUCCESS
    assert run.finished_at is not None and run.input_hash is not None
    assert run.params["candles_valid"] == 3
    assert await _count(seeded, RawIngestion) == 1


async def test_reingesting_the_same_file_is_skipped(seeded: Sessionmaker) -> None:
    await _pipeline(seeded).run(IngestMode.FULL)
    before = (await _prices(seeded), await _count(seeded, DataQualityIssue))

    second = await _pipeline(seeded).run(IngestMode.FULL)

    assert second.status is PipelineRunStatus.SKIPPED
    assert "already ingested" in (second.skip_reason or "")
    assert (await _prices(seeded), await _count(seeded, DataQualityIssue)) == before
    assert await _count(seeded, RawIngestion) == 1
    async with seeded() as s:
        run = await s.get(PipelineRun, second.run_id)
    assert run is not None and run.status is PipelineRunStatus.SKIPPED


async def test_forced_rerun_is_idempotent(seeded: Sessionmaker) -> None:
    """Even when the hash check is bypassed, UPSERT keeps the tables identical."""
    await _pipeline(seeded).run(IngestMode.FULL)
    before = (
        await _prices(seeded),
        await _count(seeded, DataQualityIssue),
        await _count(seeded, RawIngestion),
    )

    again = await _pipeline(seeded).run(IngestMode.FULL, force=True)

    assert again.status is PipelineRunStatus.SUCCESS
    assert again.rows_changed == 0  # nothing actually changed, so nothing was rewritten
    after = (
        await _prices(seeded),
        await _count(seeded, DataQualityIssue),
        await _count(seeded, RawIngestion),
    )
    assert after == before


async def test_incremental_only_adds_new_days(seeded: Sessionmaker, tmp_path: Path) -> None:
    await _pipeline(seeded).run(IngestMode.FULL)
    first = await _prices(seeded)

    # A "new download": same history, 2 more complete days (01-04 now complete, plus 01-05).
    newer = write_fixture(tmp_path / "newer.csv", days=5, partial_minutes=120)
    result = await _pipeline(seeded, newer).run(IngestMode.INCREMENTAL)

    assert result.status is PipelineRunStatus.SUCCESS
    assert result.candles_valid == 2 and result.rows_changed == 2
    after = await _prices(seeded)
    assert after[:3] == first  # history untouched
    assert [p[0].date().isoformat() for p in after[3:]] == ["2024-01-04", "2024-01-05"]
    async with seeded() as s:
        run = await s.get(PipelineRun, result.run_id)
    assert run is not None and run.params["start"] == "2024-01-04T00:00:00+00:00"
    assert await _count(seeded, RawIngestion) == 2


async def test_failed_run_is_recorded_and_file_not_marked_done(seeded: Sessionmaker) -> None:
    class ExplodingConnector(KaggleBtcConnector):
        def fetch(self, *args: object, **kwargs: object) -> FetchResult:
            raise RuntimeError("source exploded")

    pipeline = IngestionPipeline(seeded, ExplodingConnector(FIXTURE_PATH), "BTC-USD")
    with pytest.raises(RuntimeError, match="exploded"):
        await pipeline.run(IngestMode.FULL)

    async with seeded() as s:
        run = (await s.scalars(select(PipelineRun).order_by(PipelineRun.started_at.desc()))).first()
    assert run is not None and run.status is PipelineRunStatus.FAILED
    assert run.error is not None and "source exploded" in run.error
    assert await _count(seeded, RawIngestion) == 0  # a retry will process the file again
    assert await _count(seeded, MarketPrice) == 0


async def test_missing_asset_fails_with_hint(db_sessionmaker: Sessionmaker) -> None:
    with pytest.raises(LookupError, match="seed-assets"):
        await _pipeline(db_sessionmaker).run(IngestMode.FULL)
