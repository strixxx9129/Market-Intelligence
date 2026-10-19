from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient

from marketlens.core.security import create_access_token
from tests.integration.api.conftest import PASSWORD, World, bearer, login, make_user

A = "/api/v1"


# ---------------------------------------------------------------- auth flow
async def test_full_auth_flow_with_refresh_rotation(client: AsyncClient, world: World) -> None:
    r = await client.post(
        f"{A}/auth/register", json={"email": "Ada@Example.org", "password": PASSWORD}
    )
    assert r.status_code == 201 and r.json()["email"] == "ada@example.org"
    assert r.json()["role"] == "user" and r.headers["Cache-Control"] == "private, no-store"

    tokens = await login(client, "ada@example.org")
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 900
    me = await client.get(f"{A}/me", headers=bearer(tokens))
    assert me.status_code == 200 and me.json()["email"] == "ada@example.org"

    # Rotation: the refresh token works once and is replaced.
    r1 = await client.post(f"{A}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r1.status_code == 200
    rotated = r1.json()
    assert rotated["refresh_token"] != tokens["refresh_token"]

    # Reuse of the OLD token = theft signal → the whole family is revoked ...
    reuse = await client.post(f"{A}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401 and reuse.json()["error"]["code"] == "refresh_token_reused"
    # ... including the newest token the legitimate client holds.
    dead = await client.post(f"{A}/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    assert dead.status_code == 401


async def test_logout_revokes_the_session(client: AsyncClient, sm: Any, world: World) -> None:
    await make_user(sm, "bob@example.org")
    tokens = await login(client, "bob@example.org")
    assert (
        await client.post(f"{A}/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 204
    r = await client.post(f"{A}/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert r.status_code == 401


async def test_bad_credentials_are_generic(client: AsyncClient, sm: Any, world: World) -> None:
    await make_user(sm, "eve@example.org")
    wrong_pw = await client.post(
        f"{A}/auth/login", json={"email": "eve@example.org", "password": "nope"}
    )
    no_user = await client.post(
        f"{A}/auth/login", json={"email": "ghost@example.org", "password": "nope"}
    )
    assert wrong_pw.status_code == no_user.status_code == 401
    assert wrong_pw.json()["error"]["message"] == no_user.json()["error"]["message"]  # no hint


async def test_register_validation_and_duplicates(client: AsyncClient, world: World) -> None:
    short = await client.post(
        f"{A}/auth/register", json={"email": "x@example.org", "password": "short"}
    )
    bad_mail = await client.post(
        f"{A}/auth/register", json={"email": "not-an-email", "password": PASSWORD}
    )
    assert short.status_code == bad_mail.status_code == 422
    ok = await client.post(
        f"{A}/auth/register", json={"email": "dup@example.org", "password": PASSWORD}
    )
    dup = await client.post(
        f"{A}/auth/register", json={"email": "DUP@example.org", "password": PASSWORD}
    )
    assert ok.status_code == 201 and dup.status_code == 409


async def test_expired_or_forged_tokens_are_rejected(
    client: AsyncClient, sm: Any, world: World
) -> None:
    await make_user(sm, "tom@example.org")
    me = (
        await client.get(f"{A}/me", headers=bearer(await login(client, "tom@example.org")))
    ).json()
    old = create_access_token(me["id"], "user", now=datetime.now(UTC) - timedelta(hours=1))
    expired = await client.get(f"{A}/me", headers={"Authorization": f"Bearer {old}"})
    assert expired.status_code == 401 and expired.json()["error"]["code"] == "invalid_token"
    forged = await client.get(f"{A}/me", headers={"Authorization": "Bearer abc.def.ghi"})
    anonymous = await client.get(f"{A}/me")
    assert forged.status_code == anonymous.status_code == 401


# ---------------------------------------------------------------- /me resources
async def test_watchlists_crud_and_isolation(client: AsyncClient, sm: Any, world: World) -> None:
    await make_user(sm, "ann@example.org")
    await make_user(sm, "mal@example.org")
    ann = bearer(await login(client, "ann@example.org"))
    mal = bearer(await login(client, "mal@example.org"))

    r = await client.post(
        f"{A}/me/watchlists", json={"name": "crypto", "symbols": ["btc-usd"]}, headers=ann
    )
    assert r.status_code == 201 and r.json()["symbols"] == ["BTC-USD"]
    wl_id = r.json()["id"]
    assert len((await client.get(f"{A}/me/watchlists", headers=ann)).json()) == 1

    put = await client.put(
        f"{A}/me/watchlists/{wl_id}", json={"name": "renamed", "symbols": []}, headers=ann
    )
    assert put.json()["name"] == "renamed" and put.json()["symbols"] == []
    unknown = await client.post(
        f"{A}/me/watchlists", json={"name": "x", "symbols": ["DOGE-USD"]}, headers=ann
    )
    assert unknown.status_code == 422 and unknown.json()["error"]["code"] == "unknown_symbol"
    dup = await client.post(f"{A}/me/watchlists", json={"name": "renamed"}, headers=ann)
    assert dup.status_code == 409

    # Another user can't see, change or delete it, and can't tell that it exists.
    for method in ("get", "delete"):
        assert (
            await getattr(client, method)(f"{A}/me/watchlists/{wl_id}", headers=mal)
        ).status_code == 404
    assert (await client.get(f"{A}/me/watchlists", headers=mal)).json() == []

    assert (await client.delete(f"{A}/me/watchlists/{wl_id}", headers=ann)).status_code == 204
    assert (await client.get(f"{A}/me/watchlists/{wl_id}", headers=ann)).status_code == 404


async def test_alerts_crud(client: AsyncClient, sm: Any, world: World) -> None:
    await make_user(sm, "al@example.org")
    h = bearer(await login(client, "al@example.org"))
    r = await client.post(
        f"{A}/me/alerts",
        json={"symbol": "BTC-USD", "condition": "p_up_above", "threshold": 0.6},
        headers=h,
    )
    assert r.status_code == 201 and r.json()["trigger_count"] == 0
    alert_id = r.json()["id"]
    bad = await client.post(
        f"{A}/me/alerts",
        json={"symbol": "BTC-USD", "condition": "p_up_above", "threshold": 3},
        headers=h,
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_threshold"
    bad_cond = await client.post(
        f"{A}/me/alerts", json={"symbol": "BTC-USD", "condition": "moon", "threshold": 1}, headers=h
    )
    assert bad_cond.status_code == 422

    patched = await client.patch(f"{A}/me/alerts/{alert_id}", json={"is_active": False}, headers=h)
    assert patched.json()["is_active"] is False and patched.json()["threshold"] == 0.6
    assert (await client.delete(f"{A}/me/alerts/{alert_id}", headers=h)).status_code == 204
    assert (await client.get(f"{A}/me/alerts", headers=h)).json() == []


async def test_api_keys_lifecycle(client: AsyncClient, sm: Any, world: World) -> None:
    await make_user(sm, "dev@example.org")
    h = bearer(await login(client, "dev@example.org"))
    created = await client.post(f"{A}/me/api-keys", json={"name": "laptop"}, headers=h)
    assert created.status_code == 201
    key = created.json()["key"]
    assert key.startswith(f"ml_{created.json()['prefix']}_")
    listed = (await client.get(f"{A}/me/api-keys", headers=h)).json()
    assert "key" not in listed[0]  # never shown again

    me = await client.get(f"{A}/me", headers={"X-API-Key": key})
    assert me.status_code == 200 and me.json()["email"] == "dev@example.org"
    assert (
        await client.delete(f"{A}/me/api-keys/{created.json()['id']}", headers=h)
    ).status_code == 204
    revoked = await client.get(f"{A}/me", headers={"X-API-Key": key})
    assert revoked.status_code == 401 and revoked.json()["error"]["code"] == "invalid_api_key"
