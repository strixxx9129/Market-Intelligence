"""The current user's own data: watchlists, alerts (stored rules), API keys.

Every query is scoped by user_id, so one user can never see or change another's rows; a
foreign id is reported as 404, without even confirming that it exists.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.errors import ServiceError
from marketlens.core.security import new_api_key, sha256_hex
from marketlens.models import Alert, ApiKey, User, Watchlist
from marketlens.repositories import user_repo
from marketlens.schemas.auth import (
    AlertIn,
    AlertOut,
    AlertPatch,
    ApiKeyCreated,
    ApiKeyOut,
    WatchlistIn,
    WatchlistOut,
)

NOT_FOUND = "not found"


async def _asset_ids(session: AsyncSession, symbols: list[str]) -> list[int]:
    wanted = sorted({s.upper() for s in symbols})
    found = await user_repo.assets_by_symbol(session, wanted)
    missing = [s for s in wanted if s not in found]
    if missing:
        raise ServiceError(422, f"unknown symbols: {missing}", "unknown_symbol", missing)
    return [found[s].id for s in wanted]


# ---------------------------------------------------------------- watchlists
async def _wl_out(session: AsyncSession, wl: Watchlist) -> WatchlistOut:
    return WatchlistOut(
        id=wl.id,
        name=wl.name,
        symbols=await user_repo.watchlist_symbols(session, wl.id),
        created_at=wl.created_at,
        updated_at=wl.updated_at,
    )


async def list_watchlists(session: AsyncSession, user: User) -> list[WatchlistOut]:
    return [await _wl_out(session, wl) for wl in await user_repo.watchlists(session, user.id)]


async def get_watchlist(session: AsyncSession, user: User, wl_id: int) -> WatchlistOut:
    wl = await user_repo.watchlist(session, user.id, wl_id)
    if wl is None:
        raise ServiceError(404, f"watchlist {NOT_FOUND}", "watchlist_not_found")
    return await _wl_out(session, wl)


async def create_watchlist(session: AsyncSession, user: User, data: WatchlistIn) -> WatchlistOut:
    ids = await _asset_ids(session, data.symbols)
    wl = Watchlist(user_id=user.id, name=data.name)
    try:
        await user_repo.add(session, wl)
    except IntegrityError as exc:
        raise ServiceError(409, "a watchlist with this name exists", "watchlist_exists") from exc
    await user_repo.replace_items(session, wl.id, ids)
    await session.refresh(wl)
    return await _wl_out(session, wl)


async def replace_watchlist(
    session: AsyncSession, user: User, wl_id: int, data: WatchlistIn
) -> WatchlistOut:
    wl = await user_repo.watchlist(session, user.id, wl_id)
    if wl is None:
        raise ServiceError(404, f"watchlist {NOT_FOUND}", "watchlist_not_found")
    ids = await _asset_ids(session, data.symbols)
    wl.name = data.name
    await user_repo.replace_items(session, wl.id, ids)
    await session.flush()
    await session.refresh(wl)
    return await _wl_out(session, wl)


async def delete_watchlist(session: AsyncSession, user: User, wl_id: int) -> None:
    wl = await user_repo.watchlist(session, user.id, wl_id)
    if wl is None:
        raise ServiceError(404, f"watchlist {NOT_FOUND}", "watchlist_not_found")
    await session.delete(wl)


# ---------------------------------------------------------------- alerts
async def _alert_out(session: AsyncSession, a: Alert) -> AlertOut:
    symbol = (await user_repo.asset_symbols(session, [a.asset_id]))[a.asset_id]
    return AlertOut(
        id=a.id,
        symbol=symbol,
        condition=a.condition,
        threshold=a.threshold,
        is_active=a.is_active,
        last_triggered_at=a.last_triggered_at,
        trigger_count=a.trigger_count,
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


def _check_threshold(condition: str, threshold: float) -> None:
    if condition.startswith("p_up") and not 0 < threshold < 1:
        raise ServiceError(422, "p_up thresholds must be between 0 and 1", "invalid_threshold")
    if condition.startswith("price") and threshold <= 0:
        raise ServiceError(422, "price thresholds must be positive", "invalid_threshold")


async def list_alerts(session: AsyncSession, user: User) -> list[AlertOut]:
    return [await _alert_out(session, a) for a in await user_repo.alerts(session, user.id)]


async def get_alert(session: AsyncSession, user: User, alert_id: int) -> AlertOut:
    a = await user_repo.alert(session, user.id, alert_id)
    if a is None:
        raise ServiceError(404, f"alert {NOT_FOUND}", "alert_not_found")
    return await _alert_out(session, a)


async def create_alert(session: AsyncSession, user: User, data: AlertIn) -> AlertOut:
    _check_threshold(data.condition, data.threshold)
    (asset_id,) = await _asset_ids(session, [data.symbol])
    a = Alert(
        user_id=user.id,
        asset_id=asset_id,
        condition=data.condition,
        threshold=data.threshold,
        is_active=data.is_active,
        trigger_count=0,
    )
    await user_repo.add(session, a)
    await session.refresh(a)
    return await _alert_out(session, a)


async def update_alert(
    session: AsyncSession, user: User, alert_id: int, data: AlertPatch
) -> AlertOut:
    a = await user_repo.alert(session, user.id, alert_id)
    if a is None:
        raise ServiceError(404, f"alert {NOT_FOUND}", "alert_not_found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(a, field, value)
    _check_threshold(a.condition, a.threshold)
    await session.flush()
    await session.refresh(a)
    return await _alert_out(session, a)


async def delete_alert(session: AsyncSession, user: User, alert_id: int) -> None:
    a = await user_repo.alert(session, user.id, alert_id)
    if a is None:
        raise ServiceError(404, f"alert {NOT_FOUND}", "alert_not_found")
    await session.delete(a)


# ---------------------------------------------------------------- api keys
def _key_out(k: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=k.id,
        name=k.name,
        prefix=k.prefix,
        created_at=k.created_at,
        last_used_at=k.last_used_at,
        revoked_at=k.revoked_at,
    )


async def list_keys(session: AsyncSession, user: User) -> list[ApiKeyOut]:
    return [_key_out(k) for k in await user_repo.api_keys(session, user.id)]


async def create_key(session: AsyncSession, user: User, name: str) -> ApiKeyCreated:
    full, prefix = new_api_key()
    key = ApiKey(user_id=user.id, name=name, prefix=prefix, key_hash=sha256_hex(full))
    await user_repo.add(session, key)
    await session.refresh(key)
    return ApiKeyCreated(**_key_out(key).model_dump(), key=full)


async def revoke_key(session: AsyncSession, user: User, key_id: uuid.UUID) -> None:
    key = await user_repo.api_key(session, user.id, key_id)
    if key is None:
        raise ServiceError(404, f"API key {NOT_FOUND}", "api_key_not_found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
