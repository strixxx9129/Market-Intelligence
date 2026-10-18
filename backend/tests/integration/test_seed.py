from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.db.seed import SEED_ASSETS, seed_assets
from marketlens.models import Asset


async def test_seed_is_idempotent(db_session: AsyncSession) -> None:
    await seed_assets(db_session)
    await seed_assets(db_session)

    symbols = [a["symbol"] for a in SEED_ASSETS]
    count = await db_session.scalar(
        select(func.count()).select_from(Asset).where(Asset.symbol.in_(symbols))
    )
    assert count == len(SEED_ASSETS)
