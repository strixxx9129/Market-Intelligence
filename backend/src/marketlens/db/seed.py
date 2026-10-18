"""Idempotent reference-data seed. Run with `make seed` (python -m marketlens.db.seed).

Uses INSERT ... ON CONFLICT (symbol) DO UPDATE, so running it twice leaves exactly one row
per asset with the latest metadata.
"""

import asyncio
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.config import get_settings
from marketlens.core.logging import configure_logging, get_logger
from marketlens.db.session import create_engine, create_sessionmaker
from marketlens.models import Asset

logger = get_logger(__name__)

SEED_ASSETS: list[dict[str, Any]] = [
    {
        "symbol": "BTC-USD",
        "name": "Bitcoin",
        "asset_class": "crypto",
        "base_currency": "BTC",
        "quote_currency": "USD",
    },
]


async def seed_assets(session: AsyncSession) -> int:
    stmt = insert(Asset).values(SEED_ASSETS)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Asset.symbol],
        set_={
            "name": stmt.excluded.name,
            "asset_class": stmt.excluded.asset_class,
            "base_currency": stmt.excluded.base_currency,
            "quote_currency": stmt.excluded.quote_currency,
        },
    )
    await session.execute(stmt)
    return len(SEED_ASSETS)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json=settings.log_json)
    engine = create_engine(settings)
    try:
        async with create_sessionmaker(engine)() as session, session.begin():
            count = await seed_assets(session)
        logger.info("seed_complete", assets=count)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
