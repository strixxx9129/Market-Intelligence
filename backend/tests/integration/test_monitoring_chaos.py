"""Chaos test for monitoring: break the data on purpose and check the alarms go off.

Scenario: a production model trained on the first 170 of 200 days. Then
  1. ingestion "stops": the clock moves 2 days past the newest candle's close, and
  2. the last 30 days of daily returns and volatility are tripled (a regime shift, or a
     broken unit).
The real Celery tasks run (eager), and both the freshness and the drift alert must be recorded
and delivered once; a re-run inside the cooldown must be suppressed, not re-sent.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from sqlalchemy import select

from marketlens.models import (
    Feature,
    MonitoringAlert,
    MonitoringMetric,
    Prediction,
    PredictionOutcome,
)
from marketlens.monitoring import service
from marketlens.repositories import monitoring_repo
from marketlens.services.status import platform_status
from marketlens.tasks import jobs
from tests.integration.test_tasks import (  # noqa: F401  (task_env is a fixture)
    Sessionmaker,
    _setup_production_model,
    deliver,
    run,
    task_env,
)

N_DAYS = 200
TRAIN_DAYS = 170
SHIFTED = ("log_return_1d", "volatility_7d", "volatility_30d")


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def notifier(monkeypatch: pytest.MonkeyPatch) -> RecordingNotifier:
    """Stands in for the Discord/Slack webhook the tasks would use."""
    rec = RecordingNotifier()
    monkeypatch.setattr(service, "default_notifier", lambda: rec)
    return rec


@pytest.fixture
def world(task_env: None, tmp_path: Path) -> dict[str, Any]:  # noqa: F811
    mv_id = _setup_production_model(tmp_path, n_days=N_DAYS, train_days=TRAIN_DAYS)

    async def go(sm: Sessionmaker) -> dict[str, Any]:
        async with sm() as s:
            candles = await monitoring_repo.latest_candles(s)
        return {"model_id": mv_id, "last_ts": candles[0].latest_ts}

    return run(go)


def _shift_features(factor: float, days: int = 30) -> None:
    async def go(sm: Sessionmaker) -> None:
        async with sm() as s, s.begin():
            rows = (await s.scalars(select(Feature).order_by(Feature.ts.desc()).limit(days))).all()
            for f in rows:
                f.values = {
                    k: (v * factor if k in SHIFTED and v is not None else v)
                    for k, v in f.values.items()
                }

    run(go)


def _metrics(prefix: str = "") -> dict[str, MonitoringMetric]:
    async def q(sm: Sessionmaker) -> dict[str, MonitoringMetric]:
        async with sm() as s:
            rows = await s.scalars(
                select(MonitoringMetric).where(MonitoringMetric.metric_name.like(f"{prefix}%"))
            )
            return {m.metric_name: m for m in rows.all()}

    return run(q)


def _alerts() -> list[MonitoringAlert]:
    async def q(sm: Sessionmaker) -> list[MonitoringAlert]:
        async with sm() as s:
            rows = await s.scalars(select(MonitoringAlert).order_by(MonitoringAlert.id))
            return list(rows.all())

    return run(q)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------- the chaos scenario
def test_stalled_ingestion_and_shifted_features_both_alert(
    world: dict[str, Any], notifier: RecordingNotifier
) -> None:
    close = world["last_ts"] + timedelta(days=1)
    now = close + timedelta(days=2, hours=3)  # 2 simulated days without a new candle
    _shift_features(3.0)

    fresh = deliver(jobs.monitor_freshness, {"now": _iso(now)}).result
    drift = deliver(jobs.monitor_drift, {"now": _iso(now)}).result
    assert fresh["breaches"] == ["freshness_hours"]
    assert "psi:log_return_1d" in drift["breaches"]

    # Recorded in monitoring_metrics ...
    f = _metrics("freshness")["freshness_hours"]
    assert f.breached and f.value == pytest.approx(51.0) and f.threshold == 36.0
    m = _metrics("psi:")
    vol = m["psi:log_return_1d"]
    assert vol.breached and vol.value is not None and vol.value > 0.25
    assert vol.model_version_id == world["model_id"]
    assert len(vol.details["expected"]) == len(vol.details["actual"])  # bins kept for triage
    ks = _metrics("ks:")["ks:log_return_1d"]
    assert ks.value is not None and not ks.breached  # recorded as a second opinion, never alerts
    assert vol.details["level"] == "major" and vol.value > vol.details["psi_critical"]

    # ... and in monitoring_alerts, delivered through the webhook exactly once each.
    alerts = _alerts()
    assert {a.metric_name for a in alerts} >= {"freshness_hours", "psi:log_return_1d"}
    assert all(a.status == "delivered" for a in alerts)
    assert len(notifier.sent) == len(alerts)
    assert any("freshness_hours" in t and "[critical]" in t for t in notifier.sent)

    # /status surfaces them for the next 24 h.
    async def status(sm: Sessionmaker) -> Any:
        async with sm() as s:
            return await platform_status(s, now=now)

    out = run(status)
    codes = {a.code for a in out.alerts}
    assert {"monitor:freshness_hours", "monitor:psi:log_return_1d"} <= codes
    assert out.status == "degraded"

    # Re-run an hour later: still broken, still recorded, but nobody gets paged again.
    later = now + timedelta(hours=1)
    deliver(jobs.monitor_freshness, {"now": _iso(later)})
    deliver(jobs.monitor_drift, {"now": _iso(later)})
    again = _alerts()[len(alerts) :]
    assert again and all(a.status == "suppressed" for a in again)
    assert len(notifier.sent) == len(alerts)
    assert _metrics("freshness")["freshness_hours"].value == pytest.approx(52.0)

    # After the cooldown, a still-open problem alerts again.
    deliver(jobs.monitor_freshness, {"now": _iso(now + timedelta(hours=25))})
    assert _alerts()[-1].status == "delivered"


def test_healthy_data_raises_no_freshness_or_shift_alert(
    world: dict[str, Any], notifier: RecordingNotifier
) -> None:
    now = world["last_ts"] + timedelta(days=1, hours=2)  # candle closed 2 h ago
    deliver(jobs.monitor_freshness, {"now": _iso(now)})
    deliver(jobs.monitor_drift, {"now": _iso(now)})
    assert not _metrics("freshness")["freshness_hours"].breached
    clean = _metrics("psi:")["psi:log_return_1d"]
    assert clean.value is not None and not clean.breached  # same process: no alert

    _shift_features(3.0)
    deliver(jobs.monitor_drift, {"now": _iso(now + timedelta(days=1))})
    shifted = _metrics("psi:")["psi:log_return_1d"]
    assert shifted.breached and shifted.value is not None and shifted.value > 3 * clean.value
    assert not any(a.metric_name == "freshness_hours" for a in _alerts())


def test_failed_webhook_is_recorded_and_retried_next_run(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Down:
        async def send(self, text: str) -> None:
            raise ConnectionError("webhook unreachable")

    monkeypatch.setattr(service, "default_notifier", Down)
    now = world["last_ts"] + timedelta(days=3)
    deliver(jobs.monitor_freshness, {"now": _iso(now)})
    first = _alerts()
    assert [a.status for a in first] == ["failed"]
    assert "webhook unreachable" in (first[0].delivery_error or "")
    deliver(jobs.monitor_freshness, {"now": _iso(now + timedelta(hours=1))})
    assert _alerts()[-1].status == "failed"  # not suppressed: nobody was told yet


# ---------------------------------------------------------------- live performance
def _score_live(world: dict[str, Any], n: int, p_up: float, realized_up: bool) -> datetime:
    """n live forecasts, each saying `p_up`, all realised the same way."""

    async def go(sm: Sessionmaker) -> datetime:
        last = world["last_ts"]
        async with sm() as s, s.begin():
            asset_id = await monitoring_repo.asset_id_by_symbol(s, "BTC-USD")
            assert asset_id is not None
            for i in range(n):
                as_of = last - timedelta(days=n - i)
                pred = Prediction(
                    model_version_id=world["model_id"],
                    asset_id=asset_id,
                    as_of_ts=as_of,
                    target_ts=as_of + timedelta(days=1),
                    horizon=1,
                    y_pred=float(p_up >= 0.5),
                    p_up=p_up,
                    source="live",
                )
                s.add(pred)
                await s.flush()
                y = float(realized_up)
                s.add(
                    PredictionOutcome(
                        prediction_id=pred.id,
                        realized_return=0.01 if realized_up else -0.01,
                        realized_up=realized_up,
                        log_loss=-(y * np.log(p_up) + (1 - y) * np.log(1 - p_up)),
                        brier=(p_up - y) ** 2,
                        correct=(p_up >= 0.5) == realized_up,
                    )
                )
        return last + timedelta(days=1, hours=2)

    return run(go)


def test_performance_needs_enough_labels(
    world: dict[str, Any], notifier: RecordingNotifier
) -> None:
    now = _score_live(world, 5, 0.6, True)
    deliver(jobs.monitor_performance, {"now": _iso(now)})
    ll = _metrics("log_loss")["log_loss_30d"]
    assert ll.value is None and not ll.breached
    assert ll.details["n"] == 5 and "insufficient" in ll.details["reason"]


def test_confidently_wrong_model_breaches_log_loss(
    world: dict[str, Any], notifier: RecordingNotifier
) -> None:
    now = _score_live(world, 25, 0.7, False)  # 70 % up, every day went down
    result = deliver(jobs.monitor_performance, {"now": _iso(now)}).result
    m = _metrics()
    ll = m["log_loss_30d"]
    assert ll.value == pytest.approx(-np.log(0.3), abs=1e-6)  # 1.204 vs coin flip 0.693
    assert ll.breached and ll.threshold == pytest.approx(np.log(2) + 0.01, abs=1e-6)
    assert m["brier_30d"].value == pytest.approx(0.49)
    assert m["accuracy_30d"].value == 0.0 and m["accuracy_30d"].threshold == 0.0  # always-up 0
    assert "log_loss_30d" in result["breaches"]
    assert any("log_loss_30d" in t for t in notifier.sent)
