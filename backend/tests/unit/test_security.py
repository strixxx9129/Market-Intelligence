"""Edge security middleware: real client IP behind a proxy, and security headers."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from marketlens.api.security import API_CSP, ClientIPMiddleware, SecurityHeadersMiddleware
from marketlens.core.config import get_settings
from marketlens.main import create_app


def _app(header: str | None = "Fly-Client-IP", hsts: int = 0) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, hsts_max_age_s=hsts)
    app.add_middleware(ClientIPMiddleware, header=header)

    @app.get("/ip")
    async def ip(request: Request) -> dict[str, str | None]:
        return {"ip": request.client.host if request.client else None}

    @app.get("/docs-like")
    async def docs_like() -> dict[str, str]:
        return {}

    return app


def test_client_ip_comes_from_the_trusted_platform_header() -> None:
    client = TestClient(_app())
    r = client.get("/ip", headers={"Fly-Client-IP": "203.0.113.7"})
    assert r.json()["ip"] == "203.0.113.7"
    v6 = client.get("/ip", headers={"Fly-Client-IP": "2001:db8::1"})
    assert v6.json()["ip"] == "2001:db8::1"


def test_x_forwarded_for_is_never_trusted() -> None:
    """A client can send any X-Forwarded-For; using it would let anyone dodge rate limits."""
    r = TestClient(_app()).get("/ip", headers={"X-Forwarded-For": "1.2.3.4"})
    assert r.json()["ip"] == "testclient"


def test_malformed_or_unconfigured_header_keeps_the_socket_address() -> None:
    bad = TestClient(_app()).get("/ip", headers={"Fly-Client-IP": "not-an-ip; DROP TABLE"})
    assert bad.json()["ip"] == "testclient"
    off = TestClient(_app(header=None)).get("/ip", headers={"Fly-Client-IP": "203.0.113.7"})
    assert off.json()["ip"] == "testclient"  # local dev: header ignored


def test_security_headers_on_every_response() -> None:
    r = TestClient(_app()).get("/ip")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == API_CSP
    assert "strict-transport-security" not in r.headers  # dev: no HSTS on localhost
    missing = TestClient(_app()).get("/nope")
    assert missing.status_code == 404 and missing.headers["x-frame-options"] == "DENY"


def test_hsts_when_deployed() -> None:
    r = TestClient(_app(hsts=31_536_000)).get("/ip")
    assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


async def test_real_app_gets_headers_and_a_docs_policy(client: AsyncClient) -> None:
    docs = await client.get("/docs")
    assert docs.status_code == 200
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]  # Swagger assets
    api = await client.get("/healthz")
    assert api.headers["content-security-policy"] == API_CSP
    assert api.headers["x-content-type-options"] == "nosniff"


async def test_docs_can_be_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "expose_docs", False)
    app = create_app(get_settings())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/docs")).status_code == 404
        assert (await c.get("/openapi.json")).status_code == 404
        assert (await c.get("/healthz")).status_code == 200
