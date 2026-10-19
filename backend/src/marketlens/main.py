"""FastAPI application factory.

Run with: uvicorn marketlens.main:app  (or `create_app` with --factory)

The API is read-only for market data and stateless: it only SELECTs what the pipeline wrote
(rule 5), keeps no per-user state in memory (JWT/API key on every request, shared Redis for
cache and rate limits), so any number of identical replicas can run behind a load balancer.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from marketlens import __version__
from marketlens.api import health
from marketlens.api.errors import install_error_handlers
from marketlens.api.middleware import (
    IdentityMiddleware,
    RateLimitHeadersMiddleware,
    RequestIdMiddleware,
)
from marketlens.api.ratelimit import limiter
from marketlens.api.v1.router import api_router
from marketlens.core.config import Settings, get_settings
from marketlens.core.logging import configure_logging, get_logger
from marketlens.db.session import create_engine, create_sessionmaker

logger = get_logger(__name__)
DEFAULT_SECRET_PREFIX = "dev-only"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    if settings.environment == "prod" and (
        settings.jwt_secret.startswith(DEFAULT_SECRET_PREFIX) or len(settings.jwt_secret) < 32
    ):
        raise RuntimeError("refusing to start: set a strong JWT_SECRET for production")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(app.state.engine)
        app.state.redis = Redis.from_url(settings.redis_url)
        logger.info("startup", environment=settings.environment, version=__version__)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.engine.dispose()
            logger.info("shutdown")

    app = FastAPI(
        title="MarketLens API",
        version=__version__,
        description=(
            "Educational market intelligence & ML monitoring platform. **Nothing here is "
            "financial advice.**\n\nAnonymous access is rate limited per IP; send "
            "`X-API-Key` (create one under `/api/v1/me/api-keys`) for a higher limit. All "
            'errors use `{"error": {code, message, details, request_id}}`.'
        ),
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    install_error_handlers(app)

    # Middleware runs outermost-first in REVERSE order of registration:
    # RequestId → CORS → Identity → RateLimitHeaders → routes (limits: router dependency).
    app.add_middleware(RateLimitHeadersMiddleware)
    app.add_middleware(IdentityMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # bearer tokens / API keys in headers, never cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-Request-ID",
            "If-None-Match",
        ],
        expose_headers=[
            "ETag",
            "X-Request-ID",
            "X-Cache",
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
    )
    app.add_middleware(RequestIdMiddleware)

    # Probes at the root (where orchestrators expect them) and versioned under /api/v1.
    app.include_router(health.router)
    app.include_router(api_router, prefix="/api/v1")
    for route in (health.healthz, health.readyz):
        limiter.exempt(route)  # type: ignore[no-untyped-call]  # probes are never throttled
    return app


app = create_app()
