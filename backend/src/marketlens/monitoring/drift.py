"""Input drift and prediction drift for the production model.

Feature drift: the last `drift_window_days` feature rows vs the rows inside the model's
TRAINING window (train_start..train_end): the distribution the model actually learned from.
Bins are quantiles of that training data, so they're fixed per model version.

Prediction drift: the model's live P(up) over the window vs its own out-of-sample P(up) from
walk-forward validation (how it behaved when we evaluated it).

Drift is a reason to INVESTIGATE, not to retrain automatically: a shift can be a broken input
(a unit change, a stuck feed), a genuine new regime, or harmless. Retraining on a broken input
just teaches the model the bug, and retraining every time markets move chases noise. A
human looks at which bins moved, checks the data, and decides.
"""

from datetime import datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.config import get_settings
from marketlens.models import FeatureSet
from marketlens.monitoring.stats import (
    PsiResult,
    bins_for,
    ks_test,
    psi,
    psi_categorical,
    psi_critical,
)
from marketlens.monitoring.types import MetricResult
from marketlens.repositories import monitoring_repo

CATEGORICAL = {"day_of_week"}
MIN_RECENT = 10


async def check_drift(session: AsyncSession, now: datetime) -> list[MetricResult]:
    s = get_settings()
    results: list[MetricResult] = []
    for mv in await monitoring_repo.production_models(session):
        fs = await session.get(FeatureSet, mv.feature_set_id)
        asset_id = await monitoring_repo.asset_id_by_symbol(session, mv.name.split("_")[0])
        if fs is None or asset_id is None:
            continue
        latest = await monitoring_repo.latest_feature_ts(session, asset_id, fs.id, now)
        if latest is None:
            continue
        as_of = latest  # drift describes the data up to the newest feature row
        reference = await monitoring_repo.feature_rows(
            session, asset_id, fs.id, mv.train_start, mv.train_end
        )
        recent = await monitoring_repo.feature_rows(
            session, asset_id, fs.id, latest - timedelta(days=s.drift_window_days - 1), latest
        )
        names = [f["name"] for f in fs.definition["features"]]
        window: dict[str, Any] = {
            "reference": [mv.train_start.isoformat(), mv.train_end.isoformat()],
            "recent": [recent[0].ts.isoformat(), latest.isoformat()] if recent else None,
        }
        for name in names:
            ref = np.array([r.values[name] for r in reference], float)
            cur = np.array([r.values[name] for r in recent], float)
            results.extend(_feature_metrics(name, ref, cur, as_of, asset_id, mv.id, window))

        results.append(await _prediction_drift(session, mv.id, mv.name, asset_id, as_of))
    return results


def _feature_metrics(
    name: str,
    ref: np.ndarray,
    cur: np.ndarray,
    as_of: datetime,
    asset_id: int,
    model_id: int,
    window: dict[str, Any],
) -> list[MetricResult]:
    s = get_settings()
    if len(cur) < MIN_RECENT:
        return [
            MetricResult(
                f"psi:{name}",
                as_of,
                None,
                s.psi_alert,
                False,
                asset_id,
                model_id,
                details={"reason": f"only {len(cur)} recent rows", **window},
            )
        ]
    categorical = name in CATEGORICAL
    result = psi_categorical(ref, cur) if categorical else psi(ref, cur, bins_for(len(cur)))
    if result is None:
        return [
            MetricResult(
                f"psi:{name}",
                as_of,
                None,
                s.psi_alert,
                False,
                asset_id,
                model_id,
                details={"reason": "reference has no spread", **window},
            )
        ]
    level = _level(result.value)
    ks = None if categorical else ks_test(ref, cur)
    significant, critical = _significant(result, ks)
    out = [
        MetricResult(
            f"psi:{name}",
            as_of,
            round(result.value, 6),
            s.psi_alert,
            # Alert when the shift is LARGE (PSI over the line) and REAL (a significance test
            # says it isn't sampling noise; see stats.null_psi). Large-but-not-significant is
            # still recorded, with level "... (not significant)".
            result.value > s.psi_alert and significant,
            asset_id,
            model_id,
            severity="warning",
            message=(
                f"Input drift on `{name}`: PSI {result.value:.2f} (> {s.psi_alert}) for the last "
                f"{len(cur)} days vs the training data. Investigate before acting."
            ),
            details={
                **result.details(),
                "psi_critical": round(critical, 4),
                "level": level if significant or level == "none" else f"{level} (not significant)",
                **window,
            },
        )
    ]
    if ks is not None:
        stat, p = ks
        # Never alerts on its own: KS is the second opinion that gates the PSI alert.
        out.append(
            MetricResult(
                f"ks:{name}",
                as_of,
                round(p, 8),
                s.ks_alpha,
                False,
                asset_id,
                model_id,
                details={
                    "statistic": round(stat, 6),
                    "p_value": p,
                    "agrees_with_psi": (p < s.ks_alpha) == (result.value > s.psi_alert),
                },
            )
        )
    return out


def _significant(result: PsiResult, ks: tuple[float, float] | None) -> tuple[bool, float]:
    """Is the PSI more than sampling noise? Either the chi-squared reading of PSI itself or
    the KS test (better at shifts of the middle) must say so."""
    alpha = get_settings().ks_alpha
    critical = psi_critical(len(result.expected), result.n_reference, result.n_current, alpha)
    return result.value > critical or (ks is not None and ks[1] < alpha), critical


def _level(value: float) -> str:
    s = get_settings()
    return "major" if value > s.psi_alert else "moderate" if value > s.psi_warning else "none"


async def _prediction_drift(
    session: AsyncSession, model_id: int, model_name: str, asset_id: int, as_of: datetime
) -> MetricResult:
    s = get_settings()
    reference = np.array(await monitoring_repo.oos_p_up(session, model_id))
    recent = np.array(
        await monitoring_repo.live_p_up(
            session, model_id, as_of - timedelta(days=s.drift_window_days - 1), as_of
        )
    )
    if len(recent) < MIN_RECENT:
        return MetricResult(
            "psi:p_up",
            as_of,
            None,
            s.psi_alert,
            False,
            asset_id,
            model_id,
            details={"reason": f"only {len(recent)} live forecasts in the window"},
        )
    result = psi(reference, recent, bins_for(len(recent)))
    if result is None:
        return MetricResult(
            "psi:p_up",
            as_of,
            None,
            s.psi_alert,
            False,
            asset_id,
            model_id,
            details={"reason": "reference P(up) has no spread (baseline model)"},
        )
    ks = ks_test(reference, recent)
    significant, critical = _significant(result, ks)
    return MetricResult(
        "psi:p_up",
        as_of,
        round(result.value, 6),
        s.psi_alert,
        result.value > s.psi_alert and significant,
        asset_id,
        model_id,
        severity="warning",
        message=(
            f"Prediction drift for {model_name}: live P(up) distribution PSI "
            f"{result.value:.2f} vs its walk-forward predictions."
        ),
        details={
            **result.details(),
            "psi_critical": round(critical, 4),
            "level": _level(result.value),
            "ks_p_value": None if ks is None else ks[1],
        },
    )
