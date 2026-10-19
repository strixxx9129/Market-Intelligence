"""`marketlens` command-line interface (typer).

    marketlens ingest --source kaggle_btc --mode full
    marketlens ingest --source kaggle_btc --mode incremental
    marketlens seed-assets
    marketlens connectors
    marketlens build-features --feature-set v1
    marketlens features
    marketlens train --feature-set v1 --task classification
    marketlens experiment-report
    marketlens model-versions
    marketlens backtest --model-version 15 --fee-bps 10 --slippage-bps 5
    marketlens backtest-report
    marketlens admin runs | retry <run-id> | backfill --start --end | run-daily | run-weekly

Commands are thin: they build dependencies and call services, exactly like routers and tasks.
"""

import asyncio
import io
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.backtest.curves import downsample_curve
from marketlens.backtest.report import render_backtest_report
from marketlens.backtest.service import Backtester, BacktestRequest
from marketlens.core.config import get_settings
from marketlens.core.logging import configure_logging
from marketlens.core.storage import S3ObjectStore
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
from marketlens.repositories import backtest_repo, training_repo

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


@app.command("model-versions")
def model_versions(limit: Annotated[int, typer.Option(help="How many to show.")] = 30) -> None:
    """List model versions (newest first), so you know which id to backtest."""

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        async with sessionmaker() as session:
            for mv in await backtest_repo.list_model_versions(session, limit):
                typer.echo(
                    f"{mv.id:>4}  {mv.name:<32} v{mv.version:<3} {mv.task:<15} {mv.stage:<10} "
                    f"{mv.created_at:%Y-%m-%d %H:%M}"
                )

    _run_with_db(work)


@app.command()
def backtest(
    model_version: Annotated[int, typer.Option(help="model_versions.id (see model-versions).")],
    fee_bps: Annotated[float, typer.Option(help="Exchange fee per unit traded, bps.")] = 10.0,
    slippage_bps: Annotated[float, typer.Option(help="Slippage per unit traded, bps.")] = 5.0,
    execution: Annotated[str, typer.Option(help="next_open or next_close.")] = "next_open",
    long_short: Annotated[bool, typer.Option(help="Allow short positions.")] = False,
    vol_target: Annotated[
        float | None, typer.Option(help="Annualised vol target, e.g. 0.5.")
    ] = None,
) -> None:
    """Backtest a model version's out-of-sample predictions after costs (hypothetical)."""
    if execution not in ("next_open", "next_close"):
        raise typer.BadParameter("execution must be next_open or next_close")
    req = BacktestRequest(
        model_version_id=model_version,
        fee_bps=fee_bps,
        slippage_bps=slippage_bps,
        execution=execution,  # type: ignore[arg-type]
        long_short=long_short,
        vol_target=vol_target,
    )

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        bt_id, m, uri = await Backtester(sessionmaker).run(req)
        ev = m["evaluation"]
        typer.echo(
            f"backtest {bt_id}: {m['model']['name']} v{m['model']['version']}, "
            f"threshold {m['threshold']['chosen']} (chosen on selection folds)"
        )
        typer.echo(
            f"  evaluation {ev['strategy_net']['start'][:10]} → {ev['strategy_net']['end'][:10]}"
        )
        for label, key in [
            ("strategy net", "strategy_net"),
            ("strategy gross", "strategy_gross"),
            ("buy & hold", "buy_and_hold"),
            ("baseline strategy", "baseline_strategy"),
        ]:
            s = ev[key]
            typer.echo(
                f"  {label:<18} CAGR {s['cagr']:>7.1%}  Sharpe {s['sharpe']:>5.2f}  "
                f"max DD {s['max_drawdown']:>7.1%}"
            )
        typer.echo(f"  equity curve: {uri}")
        typer.echo(f"  {m['disclaimer']}")

    _run_with_db(work)


@app.command("backfill-equity-curves")
def backfill_equity_curves(
    force: Annotated[bool, typer.Option(help="Recompute curves that already exist.")] = False,
) -> None:
    """Store the weekly equity curve (for the website) on backtests created before Phase 10."""

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        store = S3ObjectStore(get_settings().backtest_bucket)
        done = 0
        async with sessionmaker() as session, session.begin():
            for bt in await backtest_repo.missing_equity_curves(session, include_existing=force):
                assert bt.equity_curve_uri is not None
                curve = pd.read_parquet(io.BytesIO(store.get_bytes(bt.equity_curve_uri)))
                bt.equity_curve = downsample_curve(curve)
                done += 1
        typer.echo(f"backfilled {done} equity curve(s)")

    _run_with_db(work)


@app.command("backtest-report")
def backtest_report() -> None:
    """Write docs/backtest-report.md from the latest backtest of each model version."""

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
        async with sessionmaker() as session:
            return render_backtest_report(await backtest_repo.latest_backtests(session))

    content = _run_with_db(work)
    path = get_settings().reports_dir / "backtest-report.md"
    path.write_text(content, encoding="utf-8", newline="\n")
    typer.echo(f"wrote {path}")


# ---------------------------------------------------------------- admin (Celery pipeline)
admin = typer.Typer(help="Operate the scheduled pipeline.", no_args_is_help=True)
app.add_typer(admin, name="admin")


def _dispatch(task_name: str, kwargs: dict[str, object], sync: bool) -> None:
    """--sync runs the task inline in this process (same retries, locks and run rows);
    otherwise it is queued for the workers."""
    from marketlens.tasks.celery_app import celery_app

    task = celery_app.tasks[task_name]
    if sync:
        result = task.apply(kwargs=kwargs)
        typer.echo(f"{task_name}: {result.state} {result.result!r}")
    else:
        typer.echo(f"queued {task_name} as {task.apply_async(kwargs=kwargs).id}")


@admin.command("runs")
def admin_runs(limit: Annotated[int, typer.Option(help="How many to show.")] = 20) -> None:
    """Recent task runs (one row per attempt)."""
    from sqlalchemy import select

    from marketlens.models import PipelineRun

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        async with sessionmaker() as s:
            runs = (
                await s.scalars(
                    select(PipelineRun)
                    .where(PipelineRun.pipeline.startswith("task:"))
                    .order_by(PipelineRun.started_at.desc())
                    .limit(limit)
                )
            ).all()
        for r in runs:
            typer.echo(
                f"{r.id}  {r.started_at:%Y-%m-%d %H:%M:%S}  {r.pipeline:<24} attempt {r.attempt}  "
                f"{r.status.value:<9} {(r.error or '')[:60]}"
            )

    _run_with_db(work)


@admin.command("retry")
def admin_retry(
    run_id: Annotated[str, typer.Argument(help="pipeline_runs.id of a failed task run.")],
    sync: Annotated[bool, typer.Option(help="Run inline instead of queueing.")] = False,
) -> None:
    """Replay a failed task run with its original parameters."""
    import uuid

    from marketlens.services.admin import plan_retry
    from marketlens.tasks import jobs  # noqa: F401  (registers tasks)

    plan = _run_with_db(lambda sm: plan_retry(sm, uuid.UUID(run_id)))
    typer.echo(f"retrying {plan.task_name} with {plan.kwargs}")
    _dispatch(plan.task_name, plan.kwargs, sync)


@admin.command("backfill")
def admin_backfill(
    start: Annotated[str, typer.Option(help="First as_of day (YYYY-MM-DD).")],
    end: Annotated[str, typer.Option(help="Last as_of day (YYYY-MM-DD).")],
    sync: Annotated[bool, typer.Option(help="Run inline instead of queueing.")] = False,
) -> None:
    """Rebuild features and write source='backfill' predictions for a date range."""
    from marketlens.tasks import jobs  # noqa: F401

    _dispatch(
        "marketlens.backfill",
        {"start": f"{start}T00:00:00+00:00", "end": f"{end}T00:00:00+00:00"},
        sync,
    )


@admin.command("run-daily")
def admin_run_daily(
    sync: Annotated[bool, typer.Option(help="Run the chain inline.")] = False,
) -> None:
    """Run ingest → validate → build_features → predict now."""
    from marketlens.tasks.jobs import daily_chain

    if sync:
        result = daily_chain().apply()
        typer.echo(f"daily chain: {result.state} {result.result!r}")
    else:
        typer.echo(f"queued daily chain {daily_chain().apply_async().id}")


@admin.command("create-user")
def admin_create_user(
    email: Annotated[str, typer.Option(help="Login e-mail.")],
    role: Annotated[str, typer.Option(help="user, contributor, maintainer or admin.")] = "user",
    password: Annotated[
        str,
        typer.Option(
            prompt=True,
            hide_input=True,
            confirmation_prompt=True,
            help="Prompted (hidden) if omitted; never pass it on the command line.",
        ),
    ] = "",
) -> None:
    """Create an account with a given role (the only way to get the first admin)."""
    from marketlens.models import Role
    from marketlens.schemas.auth import RegisterIn
    from marketlens.services import auth as auth_service

    data = RegisterIn(email=email, password=password)  # same validation as the API

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
        async with sessionmaker() as s, s.begin():
            user = await auth_service.register(s, data.email, data.password)
            user.role = Role(role)
            return f"{user.email} ({user.role.value}) id={user.id}"

    typer.echo(f"created {_run_with_db(work)}")


@admin.command("set-role")
def admin_set_role(
    email: Annotated[str, typer.Option(help="Existing account e-mail.")],
    role: Annotated[str, typer.Option(help="user, contributor, maintainer or admin.")],
) -> None:
    """Change a user's role."""
    from marketlens.models import Role
    from marketlens.repositories import user_repo

    async def work(sessionmaker: async_sessionmaker[AsyncSession]) -> str:
        async with sessionmaker() as s, s.begin():
            user = await user_repo.by_email(s, email.strip().lower())
            if user is None:
                raise typer.BadParameter(f"no user {email}")
            user.role = Role(role)
            return f"{user.email} is now {user.role.value}"

    typer.echo(_run_with_db(work))


@admin.command("run-weekly")
def admin_run_weekly(
    sync: Annotated[bool, typer.Option(help="Run the chain inline.")] = False,
) -> None:
    """Run train_candidates → promote_model now."""
    from marketlens.tasks.jobs import weekly_chain

    if sync:
        result = weekly_chain().apply()
        typer.echo(f"weekly chain: {result.state} {result.result!r}")
    else:
        typer.echo(f"queued weekly chain {weekly_chain().apply_async().id}")


if __name__ == "__main__":
    app()
