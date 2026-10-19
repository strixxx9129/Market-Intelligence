import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from marketlens.api.health import Check, get_readiness_checks


async def _ok() -> None:
    return None


async def _down() -> None:
    raise ConnectionError("connection refused")


def _override(app: FastAPI, checks: dict[str, Check]) -> None:
    app.dependency_overrides[get_readiness_checks] = lambda: checks


@pytest.mark.parametrize("path", ["/healthz", "/api/v1/healthz"])
async def test_healthz_returns_ok_without_dependencies(client: AsyncClient, path: str) -> None:
    # No lifespan ran, so there is no DB/Redis at all: liveness must still be 200.
    response = await client.get(path)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["version"] and "git_sha" in body


async def test_readyz_ok_when_all_checks_pass(app: FastAPI, client: AsyncClient) -> None:
    _override(app, {"database": _ok, "redis": _ok})

    response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}


@pytest.mark.parametrize("failing", ["database", "redis"])
async def test_readyz_503_when_a_dependency_is_down(
    app: FastAPI, client: AsyncClient, failing: str
) -> None:
    checks: dict[str, Check] = {"database": _ok, "redis": _ok}
    checks[failing] = _down
    _override(app, checks)

    response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error"
    assert body["checks"][failing] == "error"


async def test_readyz_times_out_slow_dependency(
    app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from marketlens.core.config import get_settings

    monkeypatch.setattr(get_settings(), "readiness_timeout_s", 0.05)

    async def _hang() -> None:
        await asyncio.sleep(5)

    _override(app, {"database": _hang, "redis": _ok})

    response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["database"] == "error"
