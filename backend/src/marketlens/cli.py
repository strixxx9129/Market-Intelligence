"""`marketlens` command-line interface (typer).

    marketlens ingest --source kaggle_btc --mode full
    marketlens ingest --source kaggle_btc --mode incremental
    marketlens seed-assets
    marketlens connectors
    marketlens build-features --feature-set v1
    marketlens features
    marketlens train --feature-set v1 --task classification
    marketlens experiment-report

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
from marketlens.features import FEATURES
from marketlens.features.builder import FeatureBuilder
from marketlens.features.sets import FEATURE_SETS
from marketlens.ingestion.connectors.base import available_connectors, get_connector
from marketlens.ingestion.pipeline import IngestionPipeline, IngestMode
from marketlens.ml.evaluate import METRIC_DIRECTION
from marketlens.ml.models.base import TASKS
from marketlens.ml.report import render_report
from marketlens.ml.splits import WalkForwardSplit
from marketlens.ml.train import TrainConfig, Trainer
from marketlens.models import FeatureSet, PipelineRunStatus
from marketlens.repositories import training_repo

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


@app.command("build-features")
def build_features(
    feature_set: Annotated[str, typer.Option(help="Version from FEATURE_SETS, e.g. v1.")] = "v1",
    asset: Annotated[str, typer.Option(help="Asset symbol.")] = "BTC-USD",
) -> None:
    """Compute a feature set from market_prices and UPSERT it into features (idempotent)."""

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        r = await FeatureBuilder(sessionmaker).build(feature_set, asset)
        first = r.first_ts.date() if r.first_ts is not None else "-"
        last = r.last_ts.date() if r.last_ts is not None else "-"
        typer.echo(
            f"feature set {r.feature_set} (id {r.feature_set_id}, code {r.code_hash[:12]}…)\n"
            f"  candles: {r.candles:,}   warm-up rows dropped: {r.warmup_rows}\n"
            f"  feature rows: {r.rows:,} ({first} → {last})"
            f"   inserted/changed: {r.rows_changed:,}"
        )

    _run_with_db(work)


@app.command("features")
def list_features() -> None:
    """List registered features and feature sets."""
    for name, spec in sorted(FEATURES.items()):
        typer.echo(f"{name:<24} {spec.family:<11} lookback={spec.lookback:<3} {spec.description}")
    for version, names in FEATURE_SETS.items():
        typer.echo(f"set {version}: {len(names)} features")


@app.command()
def train(
    feature_set: Annotated[str, typer.Option(help="Feature set version.")] = "v1",
    task: Annotated[str, typer.Option(help="classification or regression.")] = "classification",
    asset: Annotated[str, typer.Option(help="Asset symbol.")] = "BTC-USD",
    start: Annotated[str, typer.Option(help="First day of the modelling sample.")] = "2013-01-01",
    initial_train: Annotated[int, typer.Option(help="Rows in the first training window.")] = 730,
    test_days: Annotated[int, typer.Option(help="Rows per test fold.")] = 180,
    gap: Annotated[int, typer.Option(help="Embargo rows between train and test.")] = 5,
    window: Annotated[str, typer.Option(help="expanding or rolling.")] = "expanding",
    seed: Annotated[int, typer.Option(help="Random seed.")] = 42,
    models: Annotated[str | None, typer.Option(help="Comma-separated subset of models.")] = None,
    importance: Annotated[bool, typer.Option(help="Permutation importance on test folds.")] = True,
) -> None:
    """Train every model of a task on identical walk-forward folds; log to MLflow and the DB."""
    if task not in TASKS:
        raise typer.BadParameter(f"task must be one of {TASKS}")
    cfg = TrainConfig(
        feature_set=feature_set,
        task=task,
        asset=asset,
        start=start,
        split=WalkForwardSplit(
            initial_train=initial_train,
            test_size=test_days,
            gap=gap,
            window=window,  # type: ignore[arg-type]
        ),
        seed=seed,
        models=models.split(",") if models else None,
        importance=importance,
    )

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        r = await Trainer(sessionmaker).train(cfg)
        primary = r.primary_metric
        better = "lower" if METRIC_DIRECTION[primary] == "min" else "higher"
        typer.echo(
            f"training run {r.run_id}: {r.n_rows:,} rows, {r.n_folds} folds "
            f"(MLflow run {r.mlflow_run_id})"
        )
        typer.echo(f"  {primary} ({better} is better), mean ± std over folds:")
        for name in r.metrics["leaderboard"]:
            e = r.metrics["models"][name]
            s = e["summary"][primary]
            cmp = e["vs_best_baseline"].get(primary)
            extra = (
                f"  wins {cmp['wins']}/{cmp['folds']} vs {cmp['baseline']}"
                if cmp
                else "  (baseline)"
            )
            typer.echo(f"    {name:<20} {s['mean']:.5f} ± {s['std']:.5f}{extra}")
        v = r.metrics["verdict"]
        typer.echo(
            f"  verdict: best model {v['best_model']}, beats baseline: {v['beats_baseline']}"
        )

    _run_with_db(work)


@app.command("experiment-report")
def experiment_report() -> None:
    """Write docs/experiment-report.md from the latest successful run of each task."""

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
        runs = []
        async with sessionmaker() as session:
            for task in ("classification", "regression"):
                run = await training_repo.latest_successful_run(session, task)
                if run is not None:
                    fs = await session.get(FeatureSet, run.feature_set_id)
                    assert fs is not None
                    runs.append((run, fs))
        return render_report(runs)

    content = _run_with_db(work)
    path = get_settings().reports_dir / "experiment-report.md"
    path.write_text(content, encoding="utf-8", newline="\n")
    typer.echo(f"wrote {path}")


if __name__ == "__main__":
    app()
