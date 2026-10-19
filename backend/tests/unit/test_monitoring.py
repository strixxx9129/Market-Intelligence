"""Monitoring math and plumbing that needs no database: PSI, KS, alert text, metric labels."""

import json
import math
from datetime import UTC, datetime
from typing import Any

import httpx
import numpy as np
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from marketlens.core.observability import PrometheusMiddleware, init_sentry
from marketlens.monitoring import alerts, stats
from marketlens.monitoring.types import MetricResult

NOW = datetime(2026, 10, 19, 6, tzinfo=UTC)


# ---------------------------------------------------------------- PSI
def test_psi_matches_a_hand_calculation() -> None:
    # Two bins: 50/50 in training, 80/20 recently.
    # PSI = (0.8-0.5)*ln(0.8/0.5) + (0.2-0.5)*ln(0.2/0.5) = 0.1410 + 0.2749 = 0.4159
    expected = 0.3 * math.log(1.6) + (-0.3) * math.log(0.4)
    assert stats.psi_from_shares(np.array([0.5, 0.5]), np.array([0.8, 0.2])) == pytest.approx(
        expected
    )
    assert expected == pytest.approx(0.4159, abs=1e-4)


def test_psi_is_zero_for_identical_distributions_and_symmetric() -> None:
    e, a = np.array([0.1, 0.3, 0.6]), np.array([0.3, 0.3, 0.4])
    assert stats.psi_from_shares(e, e) == 0.0
    assert stats.psi_from_shares(e, a) == pytest.approx(stats.psi_from_shares(a, e))


def test_empty_bin_is_clipped_not_infinite() -> None:
    value = stats.psi_from_shares(np.array([0.5, 0.5]), np.array([1.0, 0.0]))
    assert math.isfinite(value) and value > 1  # a whole bin vanished: a huge shift


def test_bins_come_from_the_reference_and_outer_bins_are_open() -> None:
    ref = np.arange(1000, dtype=float)
    result = stats.psi(ref, ref)
    assert result is not None
    assert len(result.edges) == 9 and len(result.expected) == 10  # deciles
    assert result.expected == pytest.approx([0.1] * 10)
    assert result.value == pytest.approx(0.0)
    # Values far outside the training range land in the open-ended outer bins.
    far = stats.psi(ref, np.full(50, 1e9))
    assert far is not None and far.actual[-1] == 1.0 and far.value > 1


def test_same_distribution_has_small_psi_shifted_has_large() -> None:
    rng = np.random.default_rng(42)
    ref = rng.normal(0, 1, 3000)
    same = stats.psi(ref, rng.normal(0, 1, 300))
    shifted = stats.psi(ref, rng.normal(0, 3, 300))  # volatility tripled
    assert same is not None and shifted is not None
    assert same.value < 0.10
    assert shifted.value > 0.25


def test_constant_reference_or_empty_input_gives_none() -> None:
    assert stats.psi(np.full(100, 0.53), np.full(10, 0.53)) is None  # baseline P(up)
    assert stats.psi(np.array([]), np.array([1.0])) is None
    assert stats.psi(np.array([1.0, 2.0, np.nan]), np.array([np.inf])) is None


def test_categorical_psi_for_day_of_week() -> None:
    ref = np.tile(np.arange(7), 50)
    assert stats.psi_categorical(ref, np.tile(np.arange(7), 4)).value == pytest.approx(0)  # type: ignore[union-attr]
    only_mondays = stats.psi_categorical(ref, np.zeros(28))
    assert only_mondays is not None and only_mondays.value > 1
    assert only_mondays.details()["edges"] == [0, 1, 2, 3, 4, 5, 6]


def test_noise_floor_and_adaptive_bins() -> None:
    assert stats.bins_for(30) == 5 and stats.bins_for(9) == 3 and stats.bins_for(500) == 10
    # 10 bins on 30 days: noise alone is ~0.3, above the 0.25 alert line.
    assert stats.null_psi(10, 3000, 30) > 0.25
    assert stats.null_psi(5, 3000, 30) < 0.15


def _alerts_on(ref: np.ndarray, cur: np.ndarray) -> tuple[bool, bool]:
    """(naive rule: PSI > 0.25 with 10 bins, our rule: large AND significant)."""
    naive = stats.psi(ref, cur)
    r = stats.psi(ref, cur, stats.bins_for(len(cur)))
    ks = stats.ks_test(ref, cur)
    assert naive is not None and r is not None and ks is not None
    crit = stats.psi_critical(len(r.expected), r.n_reference, r.n_current, 0.01)
    return naive.value > 0.25, r.value > 0.25 and (r.value > crit or ks[1] < 0.01)


def test_drift_rule_rarely_cries_wolf_but_catches_a_real_shift() -> None:
    """Simulate 300 monitoring runs on 30 days each, with and without a real change."""
    rng = np.random.default_rng(1)
    ref = rng.normal(0, 1, 3000)
    noise = [_alerts_on(ref, rng.normal(0, 1, 30)) for _ in range(300)]
    wider = [_alerts_on(ref, rng.normal(0, 3, 30)) for _ in range(300)]  # volatility x3
    moved = [_alerts_on(ref, rng.normal(1.5, 1, 30)) for _ in range(300)]  # mean shift
    naive_false = sum(n for n, _ in noise) / 300
    ours_false = sum(o for _, o in noise) / 300
    assert naive_false > 0.3  # textbook thresholds on 30 days: alerts on noise constantly
    assert ours_false <= 0.02
    assert sum(o for _, o in wider) / 300 > 0.8
    assert sum(o for _, o in moved) / 300 > 0.8


def test_psi_critical_grows_with_bins_and_shrinks_with_data() -> None:
    small = stats.psi_critical(5, 3000, 30, 0.01)
    assert stats.psi_critical(10, 3000, 30, 0.01) > small
    assert stats.psi_critical(5, 3000, 300, 0.01) < small


# ---------------------------------------------------------------- KS
def test_ks_second_opinion() -> None:
    rng = np.random.default_rng(7)
    ref = rng.normal(0, 1, 2000)
    same = stats.ks_test(ref, rng.normal(0, 1, 200))
    diff = stats.ks_test(ref, rng.normal(1, 1, 200))
    assert same is not None and diff is not None
    assert same[1] > 0.01 and diff[1] < 1e-6
    assert stats.ks_test(ref, np.array([])) is None


# ---------------------------------------------------------------- alerts
def _result(**kw: Any) -> MetricResult:
    base: dict[str, Any] = {
        "metric_name": "freshness_hours",
        "ts": NOW,
        "value": 52.5,
        "threshold": 36.0,
        "breached": True,
        "asset_id": 1,
        "model_version_id": None,
        "severity": "critical",
        "message": "BTC-USD: no new candle for 52.5 h",
    }
    return MetricResult(**{**base, **kw})


def test_alert_key_and_text() -> None:
    r = _result()
    assert r.alert_key == "freshness_hours:1:None"
    text = alerts.format_alert(r)
    assert "[critical] freshness_hours" in text and "value 52.5" in text
    assert "threshold 36" in text and "2026-10-19 06:00 UTC" in text
    assert "value n/a" in alerts.format_alert(_result(value=None))


async def test_webhook_posts_a_discord_and_slack_compatible_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(204)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        alerts.httpx, "AsyncClient", lambda **kw: real_client(transport=transport, **kw)
    )
    await alerts.WebhookNotifier("https://hooks.example.test/x").send("hello")
    assert sent == [{"content": "hello", "text": "hello"}]


def test_default_notifier_only_with_a_url(monkeypatch: pytest.MonkeyPatch) -> None:
    from marketlens.core.config import get_settings

    monkeypatch.setattr(get_settings(), "monitoring_webhook_url", None)
    assert alerts.default_notifier() is None
    monkeypatch.setattr(get_settings(), "monitoring_webhook_url", "https://hooks.example.test/x")
    assert isinstance(alerts.default_notifier(), alerts.WebhookNotifier)


# ---------------------------------------------------------------- Prometheus / Sentry
def test_http_metrics_use_the_route_template_not_the_raw_path() -> None:
    # Nested routers with a prefix, like api/v1/router.py: the label must be the FULL template.
    inner = APIRouter()
    outer = APIRouter()
    app = FastAPI()
    app.add_middleware(PrometheusMiddleware)

    @inner.get("/things/{symbol}")
    async def thing(symbol: str) -> dict[str, str]:
        return {"symbol": symbol}

    outer.include_router(inner)
    app.include_router(outer, prefix="/api/v1")

    def count(route: str, status: str) -> float:
        value = REGISTRY.get_sample_value(
            "http_requests_total", {"method": "GET", "route": route, "status": status}
        )
        return value or 0.0

    before = count("/api/v1/things/{symbol}", "200")
    before_404 = count("unmatched", "404")
    client = TestClient(app)
    for symbol in ("BTC", "ETH", "XYZ"):
        assert client.get(f"/api/v1/things/{symbol}").status_code == 200
    assert client.get("/nope").status_code == 404
    assert count("/api/v1/things/{symbol}", "200") == before + 3  # one series for all symbols
    assert count("unmatched", "404") == before_404 + 1
    assert (
        REGISTRY.get_sample_value(
            "http_requests_total", {"method": "GET", "route": "/api/v1/things/BTC", "status": "200"}
        )
        is None
    )


def test_sentry_is_off_without_a_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    from marketlens.core.config import get_settings

    monkeypatch.setattr(get_settings(), "sentry_dsn", None)
    assert init_sentry("test") is False
