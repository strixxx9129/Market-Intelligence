"""Async SQLAlchemy engine/session factories and the FastAPI session dependency.

The engine is created in the app lifespan (see `main.py`) and stored on `app.state`, so it is
bound to the running event loop and disposed cleanly on shutdown.
"""

from collections.abc import AsyncIterator
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from marketlens.core.config import Settings


def connect_args(settings: Settings) -> dict[str, Any]:
    """asyncpg options; behind PgBouncer (transaction mode) prepared statements are off."""
    if settings.db_pgbouncer:
        return {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    return {}


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        connect_args=connect_args(settings),
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay usable after commit (needed for async, where lazy
    # refreshes would require an implicit await).
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
