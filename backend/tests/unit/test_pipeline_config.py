import pytest
import redis.exceptions
import sqlalchemy.exc
from celery.schedules import crontab

from marketlens.core.errors import PermanentError, TransientError, is_transient
from marketlens.services.promotion import evaluate_gate
from marketlens.tasks import jobs
from marketlens.tasks.celery_app import QUEUES, celery_app
from marketlens.tasks.schedules import BEAT_SCHEDULE


# ---------------------------------------------------------------- promotion gate
@pytest.mark.parametrize(
    ("cand", "base", "passed"),
    [
        ([0.680] * 10, [0.693] * 10, True),  # clearly better everywhere
        ([0.6925] * 10, [0.6927] * 10, False),  # better, but by less than the margin
        ([0.670] * 5 + [0.699] * 5, [0.693] * 10, False),  # big mean gain, worse in 5 > 3
        ([0.700] * 10, [0.693] * 10, False),  # worse
    ],
)
def test_gate_needs_margin_and_consistency(
    cand: list[float], base: list[float], passed: bool
) -> None:
    result = evaluate_gate(cand, base, min_improvement=0.001, max_worse_folds=3)
    assert result.passed is passed
    assert len(result.reasons) == 2  # every decision explains both criteria


def test_gate_reasons_and_metrics_on_btc_like_numbers() -> None:
    # Phase 5 reality: tiny average gain, worse in 14 of 24 folds → rejected.
    base = [0.693] * 24
    cand = [0.690] * 10 + [0.6935] * 14
    r = evaluate_gate(cand, base, min_improvement=0.001, max_worse_folds=8)
    assert not r.passed
    assert r.metrics["worse_folds"] == 14 and r.metrics["folds"] == 24
    assert "14/24" in r.reasons[1]


def test_gate_rejects_misaligned_folds() -> None:
    assert not evaluate_gate([0.6], [0.6, 0.7], 0.001, 3).passed


# ---------------------------------------------------------------- error classification
@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (TransientError("x"), True),
        (ConnectionRefusedError(), True),
        (TimeoutError(), True),
        (redis.exceptions.ConnectionError(), True),
        (sqlalchemy.exc.OperationalError("SELECT 1", {}, Exception("db down")), True),
        (PermanentError("bad data"), False),
        (ValueError("bug"), False),
        (FileNotFoundError("missing"), False),  # an OSError, but retrying won't create it
    ],
)
def test_transient_classification(exc: BaseException, transient: bool) -> None:
    assert is_transient(exc) is transient


# ---------------------------------------------------------------- Celery configuration
def test_reliability_defaults() -> None:
    conf = celery_app.conf
    assert conf.task_acks_late and conf.task_reject_on_worker_lost
    assert conf.task_serializer == "json" and conf.accept_content == ["json"]
    assert conf.timezone == "UTC" and conf.enable_utc
    assert conf.worker_prefetch_multiplier == 1


@pytest.mark.parametrize(
    ("task", "queue"),
    [
        ("marketlens.ingest", "ingest"),
        ("marketlens.validate", "ingest"),
        ("marketlens.build_features", "features"),
        ("marketlens.predict", "predict"),
        ("marketlens.evaluate_outcomes", "predict"),
        ("marketlens.train_candidates", "train"),
        ("marketlens.promote_model", "train"),
    ],
)
def test_routes(task: str, queue: str) -> None:
    assert celery_app.conf.task_routes[task]["queue"] == queue
    assert queue in QUEUES
    assert task in celery_app.tasks  # registered


@pytest.mark.parametrize(
    "task", [jobs.ingest, jobs.validate, jobs.build_features, jobs.predict, jobs.train_candidates]
)
def test_retry_policy(task: object) -> None:
    assert task.max_retries == 5  # type: ignore[attr-defined]
    assert task.retry_backoff and task.retry_jitter  # type: ignore[attr-defined]
    assert TransientError in task.autoretry_for  # type: ignore[attr-defined]
    assert PermanentError not in task.autoretry_for  # type: ignore[attr-defined]


def test_beat_schedule_is_utc_and_as_specified() -> None:
    assert celery_app.conf.beat_schedule == BEAT_SCHEDULE
    daily = BEAT_SCHEDULE["daily-cycle"]["schedule"]
    assert isinstance(daily, crontab)
    assert daily.hour == {0} and daily.minute == {15}
    outcomes = BEAT_SCHEDULE["evaluate-outcomes"]["schedule"]
    assert outcomes.hour == {1} and outcomes.minute == {0}
    weekly = BEAT_SCHEDULE["weekly-training"]["schedule"]
    assert weekly.hour == {2} and weekly.minute == {0} and weekly.day_of_week == {0}  # Sunday


def test_daily_chain_order_and_immutable_signatures() -> None:
    steps = jobs.daily_chain().tasks
    assert [s.task for s in steps] == [
        "marketlens.ingest",
        "marketlens.validate",
        "marketlens.build_features",
        "marketlens.predict",
    ]
    assert all(s.immutable for s in steps)
