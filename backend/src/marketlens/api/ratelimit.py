"""Rate limiting (slowapi + Redis storage, so limits are shared by every API replica).

Who is counted:
- a VALID API key  → its own bucket ("apikey:<id>"), generous limit
- a valid JWT      → per user ("user:<id>")
- anyone else      → per client IP ("ip:<addr>")

The identity is resolved and VERIFIED by `IdentityMiddleware` before the limiter runs. Keying on
the raw header instead would let an attacker invent a new fake key per request and get a fresh
bucket every time.

Implementation note: slowapi can't pass the request to a callable DEFAULT limit (it fails open,
i.e. silently stops limiting), so the middleware also stores the identity in a ContextVar that
the zero-argument limit provider reads. A test guards this.
"""

from contextvars import ContextVar

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from marketlens.core.config import get_settings

current_identity: ContextVar[str | None] = ContextVar("current_identity", default=None)


def identity_key(request: Request) -> str:
    identity = getattr(request.state, "identity", None)
    return str(identity) if identity else f"ip:{get_remote_address(request)}"


def ip_key(request: Request) -> str:
    return f"ip:{get_remote_address(request)}"


def current_limit() -> str:
    s = get_settings()
    identity = current_identity.get() or ""
    if identity.startswith("apikey:"):
        return s.rate_limit_api_key
    if identity.startswith("user:"):
        return s.rate_limit_user
    return s.rate_limit_anonymous


def build_limiter() -> Limiter:
    s = get_settings()
    return Limiter(
        key_func=identity_key,
        default_limits=[current_limit],
        storage_uri=s.redis_url,
        headers_enabled=True,  # X-RateLimit-Limit / -Remaining / -Reset and Retry-After
        enabled=s.rate_limit_enabled,
        key_prefix="ratelimit",
    )


limiter = build_limiter()


def _route_name(endpoint: object) -> str:
    return f"{endpoint.__module__}.{endpoint.__name__}"  # type: ignore[attr-defined]


async def enforce_rate_limit(request: Request) -> None:
    """Router-level dependency applying the default limits.

    Why not slowapi's middleware: FastAPI >= 0.13x keeps included routers NESTED in
    `app.routes`, and slowapi 0.1.10's middleware only scans the top level, finds no handler and
    treats every request as exempt (rate limiting silently off). By the time a dependency runs,
    FastAPI has already resolved the endpoint (`scope["endpoint"]`), so no route scan is needed.
    Routes with their own `@limiter.limit` (auth) are left to their decorator, so nothing is
    counted twice.
    """
    endpoint = request.scope.get("endpoint")
    if endpoint is None or not limiter.enabled:
        return
    name = _route_name(endpoint)
    if name in limiter._exempt_routes or name in limiter._route_limits:
        return
    limiter._check_request_limit(request, endpoint, False)
