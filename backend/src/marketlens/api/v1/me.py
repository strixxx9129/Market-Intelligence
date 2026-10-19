"""The current user's own resources. Never cached (Cache-Control: private, no-store)."""

import uuid

from fastapi import APIRouter, Depends, Response, status

from marketlens.api.deps import CurrentUser, Session, no_store
from marketlens.schemas.auth import (
    AlertIn,
    AlertOut,
    AlertPatch,
    ApiKeyCreated,
    ApiKeyIn,
    ApiKeyOut,
    UserOut,
    WatchlistIn,
    WatchlistOut,
)
from marketlens.schemas.common import ERROR_RESPONSES
from marketlens.services import users

router = APIRouter(
    prefix="/me", tags=["me"], responses=ERROR_RESPONSES, dependencies=[Depends(no_store)]
)


def _no_content() -> Response:
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})


@router.get("", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, email=user.email, role=user.role, created_at=user.created_at)


# ---------------------------------------------------------------- watchlists
@router.get("/watchlists", response_model=list[WatchlistOut])
async def list_watchlists(user: CurrentUser, session: Session) -> list[WatchlistOut]:
    return await users.list_watchlists(session, user)


@router.post("/watchlists", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def create_watchlist(body: WatchlistIn, user: CurrentUser, session: Session) -> WatchlistOut:
    out = await users.create_watchlist(session, user, body)
    await session.commit()
    return out


@router.get("/watchlists/{watchlist_id}", response_model=WatchlistOut)
async def get_watchlist(watchlist_id: int, user: CurrentUser, session: Session) -> WatchlistOut:
    return await users.get_watchlist(session, user, watchlist_id)


@router.put("/watchlists/{watchlist_id}", response_model=WatchlistOut)
async def replace_watchlist(
    watchlist_id: int, body: WatchlistIn, user: CurrentUser, session: Session
) -> WatchlistOut:
    out = await users.replace_watchlist(session, user, watchlist_id, body)
    await session.commit()
    return out


@router.delete("/watchlists/{watchlist_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_watchlist(watchlist_id: int, user: CurrentUser, session: Session) -> Response:
    await users.delete_watchlist(session, user, watchlist_id)
    await session.commit()
    return _no_content()


# ---------------------------------------------------------------- alerts (stored rules)
@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(user: CurrentUser, session: Session) -> list[AlertOut]:
    return await users.list_alerts(session, user)


@router.post("/alerts", response_model=AlertOut, status_code=status.HTTP_201_CREATED)
async def create_alert(body: AlertIn, user: CurrentUser, session: Session) -> AlertOut:
    out = await users.create_alert(session, user, body)
    await session.commit()
    return out


@router.get("/alerts/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: int, user: CurrentUser, session: Session) -> AlertOut:
    return await users.get_alert(session, user, alert_id)


@router.patch("/alerts/{alert_id}", response_model=AlertOut)
async def update_alert(
    alert_id: int, body: AlertPatch, user: CurrentUser, session: Session
) -> AlertOut:
    out = await users.update_alert(session, user, alert_id, body)
    await session.commit()
    return out


@router.delete("/alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert(alert_id: int, user: CurrentUser, session: Session) -> Response:
    await users.delete_alert(session, user, alert_id)
    await session.commit()
    return _no_content()


# ---------------------------------------------------------------- API keys
@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(user: CurrentUser, session: Session) -> list[ApiKeyOut]:
    return await users.list_keys(session, user)


@router.post("/api-keys", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_key(body: ApiKeyIn, user: CurrentUser, session: Session) -> ApiKeyCreated:
    """The full key is returned ONCE; store it safely. Send it as the `X-API-Key` header."""
    out = await users.create_key(session, user, body.name)
    await session.commit()
    return out


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_key(key_id: uuid.UUID, user: CurrentUser, session: Session) -> Response:
    await users.revoke_key(session, user, key_id)
    await session.commit()
    return _no_content()
