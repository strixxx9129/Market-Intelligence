"""SQL for users, refresh tokens, API keys, watchlists and alerts."""

import uuid
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import (
    Alert,
    ApiKey,
    Asset,
    RefreshToken,
    User,
    Watchlist,
    WatchlistItem,
)


# ---------------------------------------------------------------- users
async def by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(User.email == email))


async def add(session: AsyncSession, obj: object) -> None:
    session.add(obj)
    await session.flush()


# ---------------------------------------------------------------- refresh tokens
async def refresh_by_hash(session: AsyncSession, token_hash: str) -> RefreshToken | None:
    return await session.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
    )


async def mark_used(session: AsyncSession, token_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(RefreshToken).where(RefreshToken.id == token_id).values(used_at=now)
    )


async def revoke_family(session: AsyncSession, family_id: uuid.UUID, now: datetime) -> None:
    await session.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )


# ---------------------------------------------------------------- api keys
async def api_keys(session: AsyncSession, user_id: uuid.UUID) -> list[ApiKey]:
    q = select(ApiKey).where(ApiKey.user_id == user_id).order_by(ApiKey.created_at.desc())
    return list((await session.scalars(q)).all())


async def api_key(session: AsyncSession, user_id: uuid.UUID, key_id: uuid.UUID) -> ApiKey | None:
    return await session.scalar(
        select(ApiKey).where(ApiKey.id == key_id, ApiKey.user_id == user_id)
    )


# ---------------------------------------------------------------- assets lookup
async def assets_by_symbol(session: AsyncSession, symbols: list[str]) -> dict[str, Asset]:
    if not symbols:
        return {}
    rows = await session.scalars(select(Asset).where(Asset.symbol.in_(symbols)))
    return {a.symbol: a for a in rows.all()}


async def asset_symbols(session: AsyncSession, asset_ids: list[int]) -> dict[int, str]:
    if not asset_ids:
        return {}
    rows = await session.execute(select(Asset.id, Asset.symbol).where(Asset.id.in_(asset_ids)))
    return {r.id: r.symbol for r in rows}


# ---------------------------------------------------------------- watchlists
async def watchlists(session: AsyncSession, user_id: uuid.UUID) -> list[Watchlist]:
    q = select(Watchlist).where(Watchlist.user_id == user_id).order_by(Watchlist.id)
    return list((await session.scalars(q)).all())


async def watchlist(session: AsyncSession, user_id: uuid.UUID, wl_id: int) -> Watchlist | None:
    # Ownership is part of the WHERE clause: another user's id simply isn't found (404, not 403,
    # so we don't even confirm that the id exists).
    return await session.scalar(
        select(Watchlist).where(Watchlist.id == wl_id, Watchlist.user_id == user_id)
    )


async def watchlist_symbols(session: AsyncSession, wl_id: int) -> list[str]:
    q = (
        select(Asset.symbol)
        .join(WatchlistItem, WatchlistItem.asset_id == Asset.id)
        .where(WatchlistItem.watchlist_id == wl_id)
        .order_by(Asset.symbol)
    )
    return list((await session.scalars(q)).all())


async def replace_items(session: AsyncSession, wl_id: int, asset_ids: list[int]) -> None:
    await session.execute(delete(WatchlistItem).where(WatchlistItem.watchlist_id == wl_id))
    for asset_id in sorted(set(asset_ids)):
        session.add(WatchlistItem(watchlist_id=wl_id, asset_id=asset_id))
    await session.flush()


# ---------------------------------------------------------------- alerts
async def alerts(session: AsyncSession, user_id: uuid.UUID) -> list[Alert]:
    q = select(Alert).where(Alert.user_id == user_id).order_by(Alert.id)
    return list((await session.scalars(q)).all())


async def alert(session: AsyncSession, user_id: uuid.UUID, alert_id: int) -> Alert | None:
    return await session.scalar(select(Alert).where(Alert.id == alert_id, Alert.user_id == user_id))
