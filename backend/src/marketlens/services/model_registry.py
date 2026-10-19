"""Model leaderboard, model cards and backtests: read-only views of training results."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.errors import ServiceError
from marketlens.repositories import market_read_repo as repo
from marketlens.schemas.models import (
    BacktestOut,
    Leaderboard,
    LeaderboardEntry,
    LeaderboardList,
    MetricStat,
    ModelCard,
    PromotionDecisionOut,
)


async def leaderboard(session: AsyncSession) -> LeaderboardList:
    boards = []
    for run in await repo.latest_training_runs(session):
        m = run.metrics
        versions = {mv.algo: mv for mv in await repo.versions_of_run(session, run.id)}
        primary = m["primary_metric"]
        entries = []
        for name in m["leaderboard"]:
            e = m["models"][name]
            mv = versions.get(name)
            entries.append(
                LeaderboardEntry(
                    model_version_id=mv.id if mv else None,
                    name=mv.name if mv else name,
                    version=mv.version if mv else None,
                    family=e["family"],
                    is_baseline=e["is_baseline"],
                    stage=mv.stage if mv else None,
                    primary_metric=primary,
                    score=MetricStat(**e["summary"][primary]),
                    vs_best_baseline=e["vs_best_baseline"].get(primary),
                )
            )
        boards.append(
            Leaderboard(
                task=run.task,
                training_run_id=str(run.id),
                finished_at=run.finished_at,
                folds=len(m["folds"]),
                primary_metric=primary,
                verdict=m["verdict"],
                entries=entries,
            )
        )
    return LeaderboardList(data=boards)


async def model_card(session: AsyncSession, mv_id: int) -> ModelCard:
    mv = await repo.get_model_version(session, mv_id)
    if mv is None:
        raise ServiceError(404, f"model version {mv_id} not found", code="model_not_found")
    run = await repo.get_training_run(session, mv.training_run_id)
    fs = await repo.get_feature_set(session, mv.feature_set_id)
    assert run is not None and fs is not None
    entry: dict[str, Any] = run.metrics.get("models", {}).get(mv.algo, {})
    metrics = {
        "primary_metric": run.metrics.get("primary_metric"),
        "summary": entry.get("summary"),
        "vs_best_baseline": entry.get("vs_best_baseline"),
        "per_fold": entry.get("per_fold"),
        "eras": entry.get("eras"),
        "calibration": entry.get("calibration"),
        "importance": entry.get("importance"),
        "is_baseline": entry.get("is_baseline"),
    }
    backtests = [
        {
            "id": str(b.id),
            "created_at": b.created_at.isoformat(),
            "net_sharpe": b.metrics["evaluation"]["strategy_net"].get("sharpe"),
            "buy_and_hold_sharpe": b.metrics["evaluation"]["buy_and_hold"].get("sharpe"),
        }
        for b in await repo.backtests_for(session, mv.id)
    ]
    return ModelCard(
        id=mv.id,
        name=mv.name,
        version=mv.version,
        algo=mv.algo,
        task=mv.task,
        stage=mv.stage,
        params=mv.params,
        created_at=mv.created_at,
        train_start=mv.train_start,
        train_end=mv.train_end,
        feature_set=fs.version,
        feature_set_code_hash=fs.code_hash,
        training={
            "training_run_id": str(run.id),
            "split": run.split_config,
            "dataset_hash": run.dataset_hash,
            "git_sha": run.git_sha,
            "seed": run.seed,
            "date_range": run.metrics.get("date_range"),
        },
        metrics=metrics,
        promotion_history=[
            PromotionDecisionOut(
                decision=d.decision,
                reasons=d.reasons,
                decided_at=d.decided_at,
                candidate_model_version_id=d.candidate_model_version_id,
                production_model_version_id=d.production_model_version_id,
            )
            for d in await repo.decisions_for(session, mv.id)
        ],
        backtests=backtests,
        mlflow_run_id=mv.mlflow_run_id,
    )


async def backtest(session: AsyncSession, backtest_id: str) -> BacktestOut:
    try:
        bid = uuid.UUID(backtest_id)
    except ValueError as exc:
        raise ServiceError(404, "backtest not found", code="backtest_not_found") from exc
    bt = await repo.get_backtest(session, bid)
    if bt is None:
        raise ServiceError(404, "backtest not found", code="backtest_not_found")
    m = bt.metrics
    return BacktestOut(
        id=str(bt.id),
        model_version_id=bt.model_version_id,
        model=m["model"],
        created_at=bt.created_at,
        config=bt.config,
        threshold=m["threshold"],
        evaluation=m["evaluation"],
        selection=m["selection"],
        yearly=m["yearly"],
        sensitivity=m["sensitivity"],
        verdict=m["verdict"],
        equity_curve_uri=bt.equity_curve_uri,
        disclaimer=m["disclaimer"],
    )
