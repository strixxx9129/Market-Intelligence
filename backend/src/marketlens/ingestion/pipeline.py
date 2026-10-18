"""Ingestion service: raw source → validated daily candles in PostgreSQL.

    extract   connector.fetch()        raw file → canonical candles (+ minute-level issues)
    transform validate_candles()       split into loadable rows / quarantined rows / warnings
    load      price_repo.upsert_prices UPSERT in chunks, plus issues and the raw-file ledger

Idempotency, layer by layer:
1. File hash: an input already recorded in `raw_ingestions` is skipped (unless force=True).
2. UPSERT on the natural key: loading the same candles again changes nothing.
3. Issues UPSERT on their own natural key: re-runs don't duplicate them.
4. Atomic load: candles, issues, the ledger row and the final run status commit together.
   The `pipeline_runs` row is committed on its own first, so a crash still leaves a record.

The CLI calls this service today; a Celery task will call the same service later.
"""

import asyncio
import enum
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.logging import get_logger
from marketlens.ingestion.connectors.base import BaseConnector
from marketlens.ingestion.validation import validate_candles
from marketlens.models import IssueSeverity, PipelineRunStatus
from marketlens.repositories import ingestion_repo, price_repo

logger = get_logger(__name__)
PIPELINE_NAME = "ingest"
_PERIOD = {"1d": timedelta(days=1)}


class IngestMode(enum.StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


@dataclass
class IngestionResult:
    run_id: uuid.UUID
    status: PipelineRunStatus
    rows_read: int = 0
    candles_valid: int = 0
    candles_quarantined: int = 0
    rows_changed: int = 0
    errors: int = 0
    warnings: int = 0
    skip_reason: str | None = None
    issues_by_check: dict[str, int] = field(default_factory=dict)


class AssetNotFoundError(LookupError):
    pass


class IngestionPipeline:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        connector: BaseConnector,
        asset: str,
        interval: str = "1d",
    ) -> None:
        connector.check_supports(asset, interval)
        self.sessionmaker = sessionmaker
        self.connector = connector
        self.asset = asset
        self.interval = interval

    async def run(self, mode: IngestMode = IngestMode.FULL, force: bool = False) -> IngestionResult:
        params = {
            "source": self.connector.name,
            "asset": self.asset,
            "interval": self.interval,
            "mode": mode.value,
            "force": force,
        }
        async with self.sessionmaker() as session, session.begin():
            run_id = await ingestion_repo.create_run(session, PIPELINE_NAME, params)
        log = logger.bind(run_id=str(run_id), **params)
        log.info("ingest_started")

        try:
            result = await self._run(run_id, mode, force)
        except Exception as exc:
            async with self.sessionmaker() as session, session.begin():
                await ingestion_repo.finish_run(
                    session, run_id, PipelineRunStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
                )
            log.error("ingest_failed", error=repr(exc))
            raise
        log.info(
            "ingest_finished",
            status=result.status.value,
            rows_changed=result.rows_changed,
            quarantined=result.candles_quarantined,
            errors=result.errors,
            warnings=result.warnings,
            skip_reason=result.skip_reason,
        )
        return result

    async def _run(self, run_id: uuid.UUID, mode: IngestMode, force: bool) -> IngestionResult:
        source = self.connector.name
        # Hashing a large file is blocking I/O: run it off the event loop.
        fingerprint = await asyncio.to_thread(self.connector.fingerprint)
        input_hash = fingerprint.sha256 if fingerprint else None

        async with self.sessionmaker() as session, session.begin():
            asset_id = await ingestion_repo.get_asset_id(session, self.asset)
            if asset_id is None:
                raise AssetNotFoundError(
                    f"asset {self.asset!r} not found; run `marketlens seed-assets`"
                )
            if (
                fingerprint
                and not force
                and await ingestion_repo.is_ingested(session, source, fingerprint.sha256)
            ):
                reason = f"file already ingested (sha256 {fingerprint.sha256[:12]}…)"
                await ingestion_repo.finish_run(
                    session,
                    run_id,
                    PipelineRunStatus.SKIPPED,
                    rows_processed=0,
                    input_hash=input_hash,
                    extra_params={"skip_reason": reason},
                )
                return IngestionResult(run_id, PipelineRunStatus.SKIPPED, skip_reason=reason)
            last = (
                await price_repo.last_candle(session, asset_id, self.interval)
                if mode is IngestMode.INCREMENTAL
                else None
            )

        start = last[0] + _PERIOD[self.interval] if last else None
        previous_close = float(last[1]) if last else None

        # Extract: CPU-bound pandas work in a worker thread.
        fetched = await asyncio.to_thread(
            self.connector.fetch, self.asset, start, None, self.interval
        )
        # Transform: validation splits loadable rows from quarantined ones.
        checked = validate_candles(fetched.candles, self.interval, previous_close)
        issues = [*fetched.issues, *checked.issues]
        rows = price_repo.candles_to_rows(checked.valid, asset_id, self.interval, source)

        result = IngestionResult(
            run_id=run_id,
            status=PipelineRunStatus.SUCCESS,
            rows_read=fetched.rows_read,
            candles_valid=len(checked.valid),
            candles_quarantined=checked.quarantined,
            errors=sum(i.severity is IssueSeverity.ERROR for i in issues),
            warnings=sum(i.severity is IssueSeverity.WARNING for i in issues),
        )
        for issue in issues:
            result.issues_by_check[issue.check_name] = (
                result.issues_by_check.get(issue.check_name, 0) + 1
            )

        # Load: everything below commits atomically, or not at all.
        async with self.sessionmaker() as session, session.begin():
            result.rows_changed = await price_repo.upsert_prices(session, rows)
            await ingestion_repo.upsert_issues(session, asset_id, issues, run_id)
            if fingerprint:
                await ingestion_repo.record_raw_ingestion(
                    session, source, fingerprint, fetched.rows_read, run_id
                )
            await ingestion_repo.finish_run(
                session,
                run_id,
                PipelineRunStatus.SUCCESS,
                rows_processed=fetched.rows_read,
                input_hash=input_hash,
                extra_params={
                    "start": start.isoformat() if start else None,
                    "candles_valid": result.candles_valid,
                    "candles_quarantined": result.candles_quarantined,
                    "rows_changed": result.rows_changed,
                    "errors": result.errors,
                    "warnings": result.warnings,
                },
            )
        return result
