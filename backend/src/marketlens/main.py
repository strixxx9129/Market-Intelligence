"""FastAPI application factory.

Run with: uvicorn marketlens.main:app  (or `create_app` with --factory)
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from marketlens import __version__
from marketlens.api import health
from marketlens.api.v1.router import api_router
from marketlens.core.config import Settings, get_settings
from marketlens.core.logging import configure_logging, get_logger
from marketlens.db.session import create_engine, create_sessionmaker

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, json=settings.log_json)

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
            "Educational market intelligence & ML monitoring platform. "
            "Nothing here is financial advice."
        ),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # Probes at the root (where orchestrators expect them) and versioned under /api/v1.
    app.include_router(health.router)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
