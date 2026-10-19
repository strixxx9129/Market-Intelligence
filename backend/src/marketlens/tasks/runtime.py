"""Per-task resources for Celery workers.

Celery tasks are synchronous; our services are async. Each task therefore runs
`asyncio.run(...)`, which creates a fresh event loop, and gets a fresh engine with NullPool
inside it. A pooled engine shared across tasks would hold connections bound to a previous,
already-closed event loop, and fail in confusing ways.

Tests point the tasks at the test database / a separate Redis DB via `configure()`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from marketlens.core.config import get_settings


@dataclass
class _Overrides:
    database_url: str | None = None
    redis_url: str | None = None


_overrides = _Overrides()


def configure(database_url: str | None = None, redis_url: str | None = None) -> None:
    _overrides.database_url = database_url
    _overrides.redis_url = redis_url


def database_url() -> str:
    return _overrides.database_url or get_settings().database_url


def redis_client() -> redis.Redis:
    return redis.Redis.from_url(_overrides.redis_url or get_settings().redis_url)


@asynccontextmanager
async def task_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url(), poolclass=NullPool)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
