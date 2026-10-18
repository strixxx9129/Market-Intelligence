"""`marketlens` command-line interface (typer).

    marketlens ingest --source kaggle_btc --mode full
    marketlens ingest --source kaggle_btc --mode incremental
    marketlens seed-assets
    marketlens connectors

Commands are thin: they build dependencies and call services, exactly like routers and tasks.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.config import get_settings
from marketlens.core.logging import configure_logging
from marketlens.db.seed import seed_assets
from marketlens.db.session import create_engine, create_sessionmaker
from marketlens.ingestion.connectors.base import available_connectors, get_connector
from marketlens.ingestion.pipeline import IngestionPipeline, IngestMode
from marketlens.models import PipelineRunStatus

app = typer.Typer(help="MarketLens command-line tools.", no_args_is_help=True)


def _run_with_db[T](work: Callable[[async_sessionmaker[AsyncSession]], Awaitable[T]]) -> T:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

    async def main() -> T:
        engine = create_engine(settings)
        try:
            return await work(create_sessionmaker(engine))
        finally:
            await engine.dispose()

    return asyncio.run(main())


@app.command()
def ingest(
    source: Annotated[str, typer.Option(help="Connector name, see `marketlens connectors`.")],
    mode: Annotated[
        IngestMode, typer.Option(help="full, or only days after the last stored one.")
    ] = IngestMode.FULL,
    asset: Annotated[str, typer.Option(help="Asset symbol.")] = "BTC-USD",
    interval: Annotated[str, typer.Option(help="Candle interval.")] = "1d",
    force: Annotated[
        bool, typer.Option(help="Process the input even if already ingested.")
    ] = False,
    path: Annotated[
        Path | None, typer.Option(help="Override the raw file path (file connectors).")
    ] = None,
) -> None:
    """Ingest a source into market_prices (idempotent: safe to re-run)."""
    kwargs = {"csv_path": path} if path else {}
    connector = get_connector(source, **kwargs)

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        result = await IngestionPipeline(sessionmaker, connector, asset, interval).run(mode, force)
        typer.echo(f"run {result.run_id}: {result.status.value}")
        if result.status is PipelineRunStatus.SKIPPED:
            typer.echo(f"  skipped: {result.skip_reason}  (use --force to re-process)")
            return
        typer.echo(
            f"  raw rows read: {result.rows_read:,}\n"
            f"  candles valid: {result.candles_valid:,}"
            f"   quarantined: {result.candles_quarantined:,}\n"
            f"  rows inserted/changed: {result.rows_changed:,}\n"
            f"  issues: {result.errors:,} errors, {result.warnings:,} warnings"
        )
        for check, n in sorted(result.issues_by_check.items()):
            typer.echo(f"    {check}: {n:,}")

    _run_with_db(work)


@app.command("seed-assets")
def seed_assets_command() -> None:
    """Insert/update reference assets (idempotent)."""

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
        async with sessionmaker() as session, session.begin():
            return await seed_assets(session)

    typer.echo(f"seeded {_run_with_db(work)} asset(s)")


@app.command()
def connectors() -> None:
    """List registered connectors (built-in and installed plugins)."""
    for name in available_connectors():
        cls = type(get_connector(name))
        typer.echo(
            f"{name}: intervals={','.join(cls.supported_intervals)} "
            f"assets={','.join(cls.supported_assets) or 'any'}"
        )


if __name__ == "__main__":
    app()
