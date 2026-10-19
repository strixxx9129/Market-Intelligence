"""Live model performance over rolling 30 / 90 days.

Labels arrive LATE: a forecast made after day t closes is about day t+1, so its outcome exists
only once day t+1 has closed AND been ingested, at the earliest the following night
(evaluate_outcomes, 01:00 UTC). So performance always lags the forecasts by at least a day,
and a fresh model has no live record at all. That's why drift (which needs no labels) is the
early warning, and performance is the confirmation.

Each window compares the model with references over the SAME days:
- coin flip: log loss ln 2 ≈ 0.693, Brier 0.25
- always up: accuracy = share of up days in the window
- the model's own walk-forward expectation (mean out-of-sample log loss from training)
An alert needs at least `performance_min_n` scored forecasts; fewer is "insufficient data".
"""

import math
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.config import get_settings
from marketlens.models import TrainingRun
from marketlens.monitoring.types import MetricResult
from marketlens.repositories import monitoring_repo

WINDOWS = (30, 90)
COIN_LOG_LOSS = math.log(2)


async def check_performance(session: AsyncSession, now: datetime) -> list[MetricResult]:
    s = get_settings()
    as_of = now.replace(hour=0, minute=0, second=0, microsecond=0)
    results: list[MetricResult] = []
    for mv in await monitoring_repo.production_models(session):
        asset_id = await monitoring_repo.asset_id_by_symbol(session, mv.name.split("_")[0])
        if asset_id is None:
            continue
        run = await session.get(TrainingRun, mv.training_run_id)
        summary = run.metrics.get("models", {}).get(mv.algo, {}).get("summary", {}) if run else {}
        expected = (summary.get("log_loss") or {}).get("mean")
        reference = min(x for x in (expected, COIN_LOG_LOSS) if x is not None)
        threshold = reference + s.performance_logloss_margin
        for days in WINDOWS:
            rows = await monitoring_repo.scored_live(
                session, asset_id, as_of - timedelta(days=days), as_of
            )
            n = len(rows)
            base = {
                "window_days": days,
                "n": n,
                "walk_forward_log_loss": expected,
                "coin_flip_log_loss": COIN_LOG_LOSS,
            }
            if n < s.performance_min_n:
                results.append(
                    MetricResult(
                        f"log_loss_{days}d",
                        as_of,
                        None,
                        threshold,
                        False,
                        asset_id,
                        mv.id,
                        details={
                            **base,
                            "reason": f"insufficient data: {n} < {s.performance_min_n}",
                        },
                    )
                )
                continue
            ll = [r.log_loss for r in rows if r.log_loss is not None]
            br = [r.brier for r in rows if r.brier is not None]
            log_loss = sum(ll) / len(ll)
            accuracy = sum(r.correct for r in rows) / n
            always_up = sum(r.realized_up for r in rows) / n
            breached = log_loss > threshold
            results += [
                MetricResult(
                    f"log_loss_{days}d",
                    as_of,
                    round(log_loss, 6),
                    round(threshold, 6),
                    breached,
                    asset_id,
                    mv.id,
                    severity="critical",
                    message=(
                        f"{mv.name} v{mv.version}: live log loss {log_loss:.4f} over "
                        f"{days} days (n={n}) is worse than its reference "
                        f"{reference:.4f} + {s.performance_logloss_margin}."
                    ),
                    details=base,
                ),
                MetricResult(
                    f"brier_{days}d",
                    as_of,
                    round(sum(br) / len(br), 6),
                    0.25,
                    False,
                    asset_id,
                    mv.id,
                    details=base,
                ),
                MetricResult(
                    f"accuracy_{days}d",
                    as_of,
                    round(accuracy, 6),
                    round(always_up, 6),
                    False,
                    asset_id,
                    mv.id,
                    details={**base, "always_up_accuracy": always_up},
                ),
            ]
    return results
