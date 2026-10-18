"""Fixtures for tests against a real PostgreSQL (TEST_DATABASE_URL).

- `migrated_db` (once per session): downgrade to base, then upgrade to head. This exercises the
  real migrations in both directions instead of `metadata.create_all`, so a broken migration
  fails CI.
- `db_session` (per test): runs inside a transaction that is rolled back afterwards, so tests
  never see each other's rows and need no cleanup.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from marketlens.core.config import get_settings

BACKEND_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = get_settings().test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set; run integration tests via `make test` or CI")
    return url


@pytest.fixture(scope="session")
def migrated_db(test_database_url: str) -> str:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", test_database_url)
    cfg.attributes["configure_logger"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return test_database_url


@pytest.fixture
async def db_connection(migrated_db: str) -> AsyncIterator[AsyncConnection]:
    """One connection per test inside an outer transaction that is always rolled back."""
    engine = create_async_engine(migrated_db, poolclass=NullPool)
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield conn
        finally:
            await outer.rollback()
    await engine.dispose()


@pytest.fixture
def db_sessionmaker(db_connection: AsyncConnection) -> async_sessionmaker[AsyncSession]:
    """For code that opens its own sessions and commits (e.g. the ingestion pipeline):
    every commit becomes a SAVEPOINT inside the test's outer transaction."""
    return async_sessionmaker(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
async def db_session(
    db_sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with db_sessionmaker() as session:
        yield session
