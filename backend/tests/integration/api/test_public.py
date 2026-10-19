import base64
from typing import Any

import numpy as np
import pytest
from httpx import AsyncClient

from marketlens.services import market
from marketlens.services.prediction import PredictionService
from tests.integration.api.conftest import N_DAYS, World


async def test_assets(client: AsyncClient, world: World) -> None:
    r = await client.get("/api/v1/assets")
    assert r.status_code == 200
    (btc,) = r.json()["data"]
    assert btc["symbol"] == "BTC-USD" and btc["latest_ts"].startswith("2024-04-29")


async def test_cursor_pagination_walks_every_candle_once(client: AsyncClient, world: World) -> None:
    seen: list[str] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, Any] = {"limit": 50}
        if cursor:
            params["cursor"] = cursor
        r = await client.get("/api/v1/assets/BTC-USD/prices", params=params)
        assert r.status_code == 200
        body = r.json()
        seen += [c["ts"] for c in body["data"]]
        pages += 1
        cursor = body["page"]["next_cursor"]
        if cursor is None:
            break
    assert pages == 3 and len(seen) == N_DAYS
    assert seen == sorted(seen) and len(set(seen)) == N_DAYS  # ordered, no gaps, no dupes


async def test_price_range_filter(client: AsyncClient, world: World) -> None:
    r = await client.get(
        "/api/v1/assets/BTC-USD/prices",
        params={"from": "2024-02-01T00:00:00Z", "to": "2024-02-10T00:00:00Z"},
    )
    data = r.json()["data"]
    assert len(data) == 10 and data[0]["ts"].startswith("2024-02-01")
    assert r.json()["page"]["next_cursor"] is None


@pytest.mark.parametrize(
    "cursor", ["not-base64!!", base64.urlsafe_b64encode(b"2024-01-01").decode()]
)
async def test_bad_cursor_is_a_clean_400(client: AsyncClient, world: World, cursor: str) -> None:
    r = await client.get("/api/v1/assets/BTC-USD/prices", params={"cursor": cursor})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cursor"


async def test_errors_share_one_format_and_echo_request_id(
    client: AsyncClient, world: World
) -> None:
    r = await client.get("/api/v1/assets/NOPE/prices", headers={"X-Request-ID": "trace-123"})
    assert r.status_code == 404
    assert r.headers["X-Request-ID"] == "trace-123"
    assert r.json() == {
        "error": {
            "code": "asset_not_found",
            "message": "asset 'NOPE' not found",
            "details": None,
            "request_id": "trace-123",
        }
    }
    bad = await client.get("/api/v1/assets/BTC-USD/prices", params={"limit": 0})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"
    assert bad.json()["error"]["details"][0]["loc"][-1] == "limit"


async def test_features(client: AsyncClient, world: World) -> None:
    r = await client.get("/api/v1/assets/BTC-USD/features", params={"limit": 5})
    body = r.json()
    assert r.status_code == 200 and body["feature_set"] == "v1" and len(body["data"]) == 5
    assert len(body["features"]) == 12 and "volatility_30d" in body["data"][-1]["values"]


async def test_forecast_contract(client: AsyncClient, world: World) -> None:
    r = await client.get("/api/v1/assets/BTC-USD/forecast")
    f = r.json()
    assert r.status_code == 200
    assert f["model_version"] == "btc-usd_1d_historical_up_rate v1"
    assert f["is_baseline_model"] is True and f["model_stage"] == "production"
    assert f["as_of_ts"].startswith("2024-04-29") and f["target_ts"].startswith("2024-04-30")
    assert f["p_up"] == pytest.approx(0.52)
    assert "not financial advice" in f["disclaimer"]
    # The seeded outcome is from 2024: outside the 90 days before *today*, so it doesn't count.
    assert f["track_record"]["n"] == 0 and "only just started" in f["track_record"]["note"]


async def test_track_record_window_and_metrics(sm: Any, world: World) -> None:
    from datetime import timedelta

    async with sm() as s:
        tr = await market.track_record(s, world.asset_id, now=world.last_day + timedelta(days=2))
    assert tr.n == 1 and tr.accuracy == 1.0 and tr.always_up_accuracy == 1.0
    assert tr.log_loss == pytest.approx(-np.log(0.53)) and tr.coin_flip_log_loss == 0.6931
    assert "too few" in tr.note


async def test_models_card_backtest_status(client: AsyncClient, world: World) -> None:
    board = (await client.get("/api/v1/models")).json()["data"][0]
    assert board["task"] == "classification" and board["verdict"]["beats_baseline"] is False
    assert [e["stage"] for e in board["entries"]] == ["production", "candidate"]

    card = (await client.get(f"/api/v1/models/{world.candidate_id}")).json()
    assert card["algo"] == "logistic" and card["training"]["git_sha"] == "abc"
    assert card["metrics"]["per_fold"]["log_loss"][0] == pytest.approx(0.690)
    assert card["backtests"][0]["id"] == str(world.backtest_id)
    assert (await client.get("/api/v1/models/99999")).status_code == 404

    bt = await client.get(f"/api/v1/backtests/{world.backtest_id}")
    assert bt.status_code == 200 and bt.json()["threshold"]["chosen"] == 0.52
    assert (await client.get("/api/v1/backtests/not-a-uuid")).status_code == 404

    status = (await client.get("/api/v1/status")).json()
    codes = {a["code"] for a in status["alerts"]}
    assert "baseline_in_production" in codes and "stale_data" in codes  # data is from 2024
    assert status["status"] == "degraded"
    assert status["production_model"] == "btc-usd_1d_historical_up_rate v1"


# ---------------------------------------------------------------- caching
async def test_second_call_is_a_cache_hit_without_touching_the_db(
    client: AsyncClient, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0
    real = market.forecast

    async def counting(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return await real(*args, **kwargs)

    monkeypatch.setattr(market, "forecast", counting)
    first = await client.get("/api/v1/assets/BTC-USD/forecast")
    second = await client.get("/api/v1/assets/BTC-USD/forecast")
    assert first.headers["X-Cache"] == "MISS" and second.headers["X-Cache"] == "HIT"
    assert calls == 1  # the second response came from Redis
    assert first.content == second.content and first.headers["ETag"] == second.headers["ETag"]
    assert first.headers["Cache-Control"].startswith("public, max-age=")


async def test_etag_gives_304(client: AsyncClient, world: World) -> None:
    etag = (await client.get("/api/v1/assets")).headers["ETag"]
    r = await client.get("/api/v1/assets", headers={"If-None-Match": etag})
    assert r.status_code == 304 and r.content == b""
    stale = await client.get("/api/v1/assets", headers={"If-None-Match": '"other"'})
    assert stale.status_code == 200


async def test_new_prediction_invalidates_the_cached_forecast(
    client: AsyncClient, sm: Any, world: World
) -> None:
    before = await client.get("/api/v1/assets/BTC-USD/forecast")
    assert (await client.get("/api/v1/assets/BTC-USD/forecast")).headers["X-Cache"] == "HIT"

    class Stub:
        def predict_proba(self, X: Any) -> Any:
            return np.tile([0.3, 0.7], (len(X), 1))

    # The pipeline writes a new forecast (same as the Celery predict task would).
    await PredictionService(sm, model_loader=lambda mv: Stub()).predict("BTC-USD")

    after = await client.get("/api/v1/assets/BTC-USD/forecast")
    assert after.headers["X-Cache"] == "MISS"  # namespace version bumped → old entry unreachable
    assert after.json()["p_up"] == pytest.approx(0.7) != before.json()["p_up"]
