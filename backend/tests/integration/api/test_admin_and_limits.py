from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from marketlens.api.ratelimit import limiter
from marketlens.core.config import get_settings
from marketlens.models import ModelVersion, PromotionDecision, Role
from marketlens.tasks.celery_app import celery_app
from tests.integration.api.conftest import World, bearer, login, make_user

A = "/api/v1"


# ---------------------------------------------------------------- roles & admin
async def test_admin_endpoints_need_the_admin_role(
    client: AsyncClient, sm: Any, world: World
) -> None:
    await make_user(sm, "user@example.org")
    await make_user(sm, "maint@example.org", Role.MAINTAINER)
    url = f"{A}/admin/runs/{world.failed_run_id}/retry"
    assert (await client.post(url)).status_code == 401
    for email in ("user@example.org", "maint@example.org"):  # below admin, even maintainer
        r = await client.post(url, headers=bearer(await login(client, email)))
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


async def test_admin_retry_enqueues_with_original_params(
    client: AsyncClient, sm: Any, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    class FakeResult:
        id = "celery-123"

    def fake_send(name: str, kwargs: dict[str, Any]) -> FakeResult:
        sent.append((name, kwargs))
        return FakeResult()

    monkeypatch.setattr(celery_app, "send_task", fake_send)
    await make_user(sm, "root@example.org", Role.ADMIN)
    h = bearer(await login(client, "root@example.org"))

    r = await client.post(f"{A}/admin/runs/{world.failed_run_id}/retry", headers=h)
    assert r.status_code == 202 and r.json()["celery_task_id"] == "celery-123"
    assert sent == [
        ("marketlens.ingest", {"asset": "BTC-USD", "source": "kaggle_btc", "mode": "incremental"})
    ]
    missing = await client.post(
        f"{A}/admin/runs/00000000-0000-0000-0000-000000000000/retry", headers=h
    )
    assert missing.status_code == 404


async def test_admin_promote_respects_the_gate_and_audits_force(
    client: AsyncClient, sm: Any, world: World
) -> None:
    await make_user(sm, "root@example.org", Role.ADMIN)
    h = bearer(await login(client, "root@example.org"))
    url = f"{A}/admin/models/{world.candidate_id}/promote"

    rejected = await client.post(url, json={}, headers=h)
    assert rejected.status_code == 200 and rejected.json()["decision"] == "rejected"
    assert rejected.json()["production_model_version_id"] == world.model_id

    no_reason = await client.post(url, json={"force": True}, headers=h)
    assert no_reason.status_code == 422 and no_reason.json()["error"]["code"] == "reason_required"

    forced = await client.post(url, json={"force": True, "reason": "demo of override"}, headers=h)
    assert forced.json()["decision"] == "manual"
    assert any("OVERRIDDEN: demo of override" in r for r in forced.json()["reasons"])

    async with sm() as s:
        stages = {mv.algo: mv.stage for mv in (await s.scalars(select(ModelVersion))).all()}
        decisions = [
            (d.decision, d.metrics.get("actor"))
            for d in (
                await s.scalars(select(PromotionDecision).order_by(PromotionDecision.id))
            ).all()
        ]
    assert stages == {"logistic": "production", "historical_up_rate": "archived"}
    assert decisions == [
        ("rejected", "admin:root@example.org"),
        ("manual", "admin:root@example.org"),
    ]
    # The public forecast cache was invalidated: the new production model is visible at once.
    board = (await client.get(f"{A}/models")).json()["data"][0]["entries"]
    assert {e["name"]: e["stage"] for e in board}["btc-usd_1d_logistic"] == "production"


# ---------------------------------------------------------------- rate limiting
@pytest.fixture
def limits_on(monkeypatch: pytest.MonkeyPatch) -> None:
    s = get_settings()
    monkeypatch.setattr(s, "rate_limit_anonymous", "3/minute")
    monkeypatch.setattr(s, "rate_limit_api_key", "10/minute")
    limiter.enabled = True
    limiter.reset()


async def test_anonymous_rate_limit_per_ip(
    client: AsyncClient, world: World, limits_on: None
) -> None:
    codes = [(await client.get(f"{A}/assets")).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = await client.get(f"{A}/assets")
    assert r.json()["error"]["code"] == "rate_limited"
    assert int(r.headers["Retry-After"]) > 0 and r.headers["X-RateLimit-Limit"] == "3"
    ok = await client.get("/healthz")
    assert ok.status_code == 200  # probes are never throttled


async def test_api_key_has_its_own_bucket_but_fake_keys_do_not(
    client: AsyncClient, sm: Any, world: World, limits_on: None
) -> None:
    await make_user(sm, "dev@example.org")
    h = bearer(await login(client, "dev@example.org"))
    limiter.reset()  # the login/register calls above don't count against this test
    key = (await client.post(f"{A}/me/api-keys", json={"name": "k"}, headers=h)).json()["key"]
    limiter.reset()

    for _ in range(3):
        await client.get(f"{A}/assets")
    assert (await client.get(f"{A}/assets")).status_code == 429  # this IP is exhausted
    keyed = [
        (await client.get(f"{A}/assets", headers={"X-API-Key": key})).status_code for _ in range(5)
    ]
    assert keyed == [200] * 5  # a valid key counts against its own (bigger) bucket
    fake = await client.get(f"{A}/assets", headers={"X-API-Key": "ml_deadbeef_made-up-secret"})
    assert fake.status_code == 429  # an invented key earns nothing: still the IP bucket


async def test_login_is_throttled_per_ip(
    client: AsyncClient, sm: Any, world: World, limits_on: None
) -> None:
    codes = [
        (
            await client.post(
                f"{A}/auth/login", json={"email": "x@example.org", "password": "guess"}
            )
        ).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 429  # 5/minute against credential stuffing
