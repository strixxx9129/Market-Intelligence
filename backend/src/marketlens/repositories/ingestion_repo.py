"""SQL for pipeline bookkeeping: pipeline_runs, raw_ingestions, data_quality_issues, assets."""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.ingestion.types import QualityIssue, SourceFingerprint
from marketlens.models import (
    Asset,
    DataQualityIssue,
    PipelineRun,
    PipelineRunStatus,
    RawIngestion,
)

ISSUE_CHUNK_SIZE = 5_000


async def get_asset_id(session: AsyncSession, symbol: str) -> int | None:
    return await session.scalar(select(Asset.id).where(Asset.symbol == symbol))


# ---------------------------------------------------------------- pipeline_runs
async def create_run(session: AsyncSession, pipeline: str, params: dict[str, Any]) -> uuid.UUID:
    run = PipelineRun(pipeline=pipeline, status=PipelineRunStatus.RUNNING, params=params)
    session.add(run)
    await session.flush()
    return run.id


async def finish_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    status: PipelineRunStatus,
    *,
    rows_processed: int | None = None,
    input_hash: str | None = None,
    error: str | None = None,
    extra_params: dict[str, Any] | None = None,
) -> None:
    values: dict[str, Any] = {
        "status": status,
        "finished_at": func.now(),
        "rows_processed": rows_processed,
        "input_hash": input_hash,
        "error": error,
    }
    if extra_params:
        # JSONB || merges the run's summary (counts, skip reason) into its params.
        values["params"] = PipelineRun.params.op("||")(extra_params)
    await session.execute(update(PipelineRun).where(PipelineRun.id == run_id).values(**values))


# ---------------------------------------------------------------- raw_ingestions
async def is_ingested(session: AsyncSession, source: str, file_hash: str) -> bool:
    found = await session.scalar(
        select(RawIngestion.id).where(
            RawIngestion.source == source, RawIngestion.file_hash == file_hash
        )
    )
    return found is not None


async def record_raw_ingestion(
    session: AsyncSession,
    source: str,
    fingerprint: SourceFingerprint,
    rows_read: int,
    run_id: uuid.UUID,
) -> None:
    stmt = insert(RawIngestion).values(
        source=source,
        uri=fingerprint.uri,
        file_hash=fingerprint.sha256,
        bytes=fingerprint.bytes,
        rows_read=rows_read,
        pipeline_run_id=run_id,
    )
    # --force re-runs of an already-recorded file keep the original ledger entry.
    await session.execute(
        stmt.on_conflict_do_nothing(constraint="uq_raw_ingestions_source_file_hash")
    )


# ---------------------------------------------------------------- data_quality_issues
async def upsert_issues(
    session: AsyncSession, asset_id: int, issues: Sequence[QualityIssue], run_id: uuid.UUID
) -> int:
    """UPSERT on (asset_id, interval, ts, check_name): seeing the same problem again updates
    `last_seen_at`/`details` instead of creating a duplicate issue."""
    if not issues:
        return 0
    stmt = insert(DataQualityIssue)
    excluded = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_data_quality_issues_asset_id_interval_ts_check_name",
        set_={
            "severity": excluded.severity,
            "details": excluded.details,
            "pipeline_run_id": excluded.pipeline_run_id,
            "last_seen_at": func.now(),
        },
    )
    # One statement may not hit the same conflict key twice, so collapse repeats first
    # (e.g. a minute that appears three times yields two duplicate_timestamp findings).
    by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for i in issues:
        key = (i.interval, i.ts, i.check_name)
        if key in by_key:
            by_key[key]["details"] = {
                **i.details,
                "occurrences": by_key[key]["details"].get("occurrences", 1) + 1,
            }
            continue
        by_key[key] = {
            "asset_id": asset_id,
            "interval": i.interval,
            "ts": i.ts,
            "check_name": i.check_name,
            "severity": i.severity,
            "details": i.details,
            "pipeline_run_id": run_id,
        }
    rows = list(by_key.values())
    for start in range(0, len(rows), ISSUE_CHUNK_SIZE):
        await session.execute(stmt, rows[start : start + ISSUE_CHUNK_SIZE])
    return len(rows)
