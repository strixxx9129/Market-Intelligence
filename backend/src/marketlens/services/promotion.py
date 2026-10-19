"""Model promotion: candidate → staging → production, behind a gate.

Why a gate? Training every week always produces SOME "best" model. Without a gate, production
would swap to whichever model got lucky on this week's folds, and users would get noise that
changes every Sunday. The gate asks the same question as Phase 5: does the candidate beat the
best feature-free baseline of the same training run, by a meaningful margin, consistently?

    promote  iff  mean_fold(baseline log loss - candidate log loss) >= min_improvement
             and  folds where the candidate is worse <= max_worse_folds

If no candidate passes and nothing serves yet, the best BASELINE becomes production (an honest
fallback: forecasts keep flowing, clearly labelled with the baseline's model version).
Every decision is recorded with its reasons.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.cache import invalidate_namespaces
from marketlens.core.config import get_settings
from marketlens.core.errors import ServiceError
from marketlens.core.logging import get_logger
from marketlens.models import ModelVersion, TrainingRun
from marketlens.repositories import backtest_repo, ingestion_repo, prediction_repo

logger = get_logger(__name__)


@dataclass
class GateResult:
    passed: bool
    reasons: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)


def evaluate_gate(
    candidate_folds: list[float],
    baseline_folds: list[float],
    min_improvement: float,
    max_worse_folds: int,
) -> GateResult:
    """Pure decision function: per-fold log loss of candidate vs baseline (lower is better)."""
    cand, base = np.asarray(candidate_folds, float), np.asarray(baseline_folds, float)
    if len(cand) == 0 or len(cand) != len(base):
        return GateResult(False, ["fold metrics missing or misaligned"])
    improvement = base - cand  # positive = candidate better
    mean_improvement = float(np.mean(improvement))
    worse = int(np.sum(improvement < 0))
    reasons = []
    ok_margin = mean_improvement >= min_improvement
    ok_worse = worse <= max_worse_folds
    reasons.append(
        f"mean log-loss improvement {mean_improvement:+.5f} "
        f"{'>=' if ok_margin else '<'} required {min_improvement:.5f}"
    )
    reasons.append(
        f"worse than baseline in {worse}/{len(cand)} folds "
        f"{'<=' if ok_worse else '>'} allowed {max_worse_folds}"
    )
    return GateResult(
        passed=ok_margin and ok_worse,
        reasons=reasons,
        metrics={
            "mean_improvement": mean_improvement,
            "worse_folds": worse,
            "folds": len(cand),
            "candidate_mean_log_loss": float(np.mean(cand)),
            "baseline_mean_log_loss": float(np.mean(base)),
        },
    )


def _fold_log_loss(run: TrainingRun, algo: str) -> list[float]:
    return list(run.metrics["models"][algo]["per_fold"]["log_loss"])


def _best_baseline_algo(run: TrainingRun) -> str:
    best = run.metrics["verdict"]["best_model"]
    if best and "log_loss" in run.metrics["models"][best]["vs_best_baseline"]:
        return str(run.metrics["models"][best]["vs_best_baseline"]["log_loss"]["baseline"])
    baselines = [n for n, e in run.metrics["models"].items() if e["is_baseline"]]
    return str(
        min(baselines, key=lambda n: run.metrics["models"][n]["summary"]["log_loss"]["mean"])
    )


class PromotionService:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker
        self.settings = get_settings()

    async def mark_staging(self, training_run_id: uuid.UUID) -> ModelVersion | None:
        """After training: the run's best non-baseline model becomes the staging candidate."""
        async with self.sessionmaker() as s, s.begin():
            run = await backtest_repo.get_training_run(s, training_run_id)
            assert run is not None
            best = run.metrics["verdict"]["best_model"]
            if not best:
                return None
            mv = await prediction_repo.version_by_algo(s, run.id, best)
            assert mv is not None
            prefix_asset = mv.name.split("_")[0]
            for old in await prediction_repo.models_in_stage(s, prefix_asset, "staging", run.task):
                await prediction_repo.set_stage(s, old.id, "candidate")
            await prediction_repo.set_stage(s, mv.id, "staging")
            return mv

    async def promote(self, asset: str, pipeline_run_id: uuid.UUID | None = None) -> dict[str, Any]:
        result = await self._promote(asset, pipeline_run_id)
        # After the transaction committed, whichever path was taken (promote / reject / fallback).
        await invalidate_namespaces("models", "forecast", "status")
        return result

    async def _promote(self, asset: str, pipeline_run_id: uuid.UUID | None) -> dict[str, Any]:
        async with self.sessionmaker() as s, s.begin():
            staging = await prediction_repo.models_in_stage(s, asset, "staging")
            current = await prediction_repo.production_model(s, asset)
            if not staging:
                if current is None:
                    return await self._fallback(s, asset, pipeline_run_id, "no staging candidate")
                return {
                    "decision": "noop",
                    "reasons": ["no staging candidate"],
                    "production": current.id,
                }
            cand = staging[0]
            run = await backtest_repo.get_training_run(s, cand.training_run_id)
            assert run is not None
            baseline_algo = _best_baseline_algo(run)
            baseline = await prediction_repo.version_by_algo(s, run.id, baseline_algo)
            assert baseline is not None
            gate = evaluate_gate(
                _fold_log_loss(run, cand.algo),
                _fold_log_loss(run, baseline_algo),
                self.settings.promotion_min_logloss_improvement,
                self.settings.promotion_max_worse_folds,
            )
            if gate.passed:
                if current is not None and current.id != cand.id:
                    await prediction_repo.set_stage(s, current.id, "archived")
                await prediction_repo.set_stage(s, cand.id, "production")
                production, decision = cand, "promoted"
            else:
                await prediction_repo.set_stage(s, cand.id, "candidate")
                if current is None:
                    await prediction_repo.set_stage(s, baseline.id, "production")
                    gate.reasons.append(
                        f"nothing in production: serving baseline `{baseline_algo}` as fallback"
                    )
                    production = baseline
                else:
                    production = current
                decision = "rejected"
            await prediction_repo.record_decision(
                s,
                candidate_model_version_id=cand.id,
                baseline_model_version_id=baseline.id,
                production_model_version_id=production.id,
                decision=decision,
                reasons=gate.reasons,
                metrics={
                    **gate.metrics,
                    "candidate": cand.name,
                    "baseline": baseline_algo,
                    "min_improvement": self.settings.promotion_min_logloss_improvement,
                    "max_worse_folds": self.settings.promotion_max_worse_folds,
                },
                pipeline_run_id=pipeline_run_id,
            )
        logger.info(
            "promotion_decision",
            decision=decision,
            candidate=cand.name,
            production=production.name,
            reasons=gate.reasons,
        )
        return {
            "decision": decision,
            "reasons": gate.reasons,
            "production": production.id,
            "candidate": cand.id,
        }

    async def promote_version(
        self, model_version_id: int, force: bool, reason: str | None, actor: str
    ) -> dict[str, Any]:
        """Admin action on one specific version. Same gate as the weekly job; `force` overrides
        a failing gate but requires a reason, and is recorded as a 'manual' decision."""
        if force and not (reason and reason.strip()):
            raise ServiceError(422, "force requires a reason", "reason_required")
        async with self.sessionmaker() as s, s.begin():
            cand = await s.get(ModelVersion, model_version_id)
            if cand is None:
                raise ServiceError(404, "model version not found", "model_not_found")
            if cand.task != "classification":
                raise ServiceError(422, "only classification models serve forecasts", "wrong_task")
            run = await backtest_repo.get_training_run(s, cand.training_run_id)
            assert run is not None
            baseline_algo = _best_baseline_algo(run)
            baseline = await prediction_repo.version_by_algo(s, run.id, baseline_algo)
            assert baseline is not None
            gate = evaluate_gate(
                _fold_log_loss(run, cand.algo),
                _fold_log_loss(run, baseline_algo),
                self.settings.promotion_min_logloss_improvement,
                self.settings.promotion_max_worse_folds,
            )
            reasons = [f"requested by {actor}", *gate.reasons]
            asset = cand.name.split("_")[0]
            current = await prediction_repo.production_model(s, asset)
            if gate.passed or force:
                decision = "promoted" if gate.passed else "manual"
                if not gate.passed:
                    reasons.append(f"gate OVERRIDDEN: {reason}")
                if current is not None and current.id != cand.id:
                    await prediction_repo.set_stage(s, current.id, "archived")
                await prediction_repo.set_stage(s, cand.id, "production")
                production_id: int | None = cand.id
            else:
                decision = "rejected"
                production_id = current.id if current else None
            await prediction_repo.record_decision(
                s,
                candidate_model_version_id=cand.id,
                baseline_model_version_id=baseline.id,
                production_model_version_id=production_id,
                decision=decision,
                reasons=reasons,
                metrics={**gate.metrics, "actor": actor, "forced": force and not gate.passed},
            )
        await invalidate_namespaces("models", "forecast", "status")
        logger.info("manual_promotion", decision=decision, model=cand.name, actor=actor)
        return {"decision": decision, "reasons": reasons, "production": production_id}

    async def ensure_production(
        self, asset: str, pipeline_run_id: uuid.UUID | None = None
    ) -> ModelVersion:
        """Return the production model, installing the baseline fallback if none exists."""
        async with self.sessionmaker() as s, s.begin():
            current = await prediction_repo.production_model(s, asset)
            if current is not None:
                return current
            await self._fallback(s, asset, pipeline_run_id, "no production model")
            current = await prediction_repo.production_model(s, asset)
            assert current is not None
        await invalidate_namespaces("models", "forecast", "status")  # after the commit
        return current

    async def _fallback(
        self, s: AsyncSession, asset: str, pipeline_run_id: uuid.UUID | None, why: str
    ) -> dict[str, Any]:
        asset_id = await ingestion_repo.get_asset_id(s, asset)
        if asset_id is None:
            raise LookupError(f"asset {asset!r} not found")
        run = await prediction_repo.latest_training_run(s, "classification", asset_id)
        if run is None:
            raise LookupError("no successful classification training run: run `make train`")
        algo = _best_baseline_algo(run)
        baseline = await prediction_repo.version_by_algo(s, run.id, algo)
        assert baseline is not None
        await prediction_repo.set_stage(s, baseline.id, "production")
        reasons = [why, f"serving best baseline `{algo}` (v{baseline.version}) as fallback"]
        await prediction_repo.record_decision(
            s,
            candidate_model_version_id=None,
            baseline_model_version_id=baseline.id,
            production_model_version_id=baseline.id,
            decision="fallback",
            reasons=reasons,
            metrics={"training_run_id": str(run.id)},
            pipeline_run_id=pipeline_run_id,
        )
        return {"decision": "fallback", "reasons": reasons, "production": baseline.id}
