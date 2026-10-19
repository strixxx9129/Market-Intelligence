"""FastAPI dependencies shared by the v1 routers."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.api.errors import ApiError
from marketlens.core.cache import TTL, Cache, etag_for
from marketlens.core.security import InvalidTokenError, decode_access_token
from marketlens.db.session import get_session
from marketlens.models import Role, User

Session = Annotated[AsyncSession, Depends(get_session)]


def get_cache(request: Request) -> Cache:
    return Cache(request.app.state.redis)


CacheDep = Annotated[Cache, Depends(get_cache)]


# ---------------------------------------------------------------- auth
async def optional_user(request: Request, session: Session) -> User | None:
    api_key_user: User | None = getattr(request.state, "api_key_user", None)
    if api_key_user is not None:
        return api_key_user
    if request.headers.get("X-API-Key"):
        raise ApiError(401, "invalid or revoked API key", code="invalid_api_key")
    auth = request.headers.get("Authorization", "")
    if not auth:
        return None
    if not auth.lower().startswith("bearer "):
        raise ApiError(401, "use 'Authorization: Bearer <token>'", code="invalid_token")
    try:
        claims = decode_access_token(auth[7:].strip())
    except InvalidTokenError as exc:
        raise ApiError(
            401,
            "invalid or expired access token",
            code="invalid_token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = await session.get(User, claims.user_id)
    if user is None or not user.is_active:
        raise ApiError(401, "user not found or disabled", code="invalid_token")
    return user


async def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise ApiError(
            401,
            "authentication required",
            code="unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(minimum: Role) -> Callable[[User], Awaitable[User]]:
    """Roles are ordered user < contributor < maintainer < admin; higher includes lower."""

    async def check(user: CurrentUser) -> User:
        if user.role.level < minimum.level:
            raise ApiError(403, f"requires role '{minimum.value}'", code="forbidden")
        return user

    return check


AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]


# ---------------------------------------------------------------- cached public responses
async def cached_response(
    request: Request,
    cache: Cache,
    namespace: str,
    compute: Callable[[], Awaitable[BaseModel]],
    query: dict[str, Any] | None = None,
) -> Response:
    """Cache-aside + HTTP caching for PUBLIC data (never for per-user responses).

    1. Redis: return the stored JSON on a hit; otherwise compute from PostgreSQL and store it.
    2. HTTP: ETag (hash of the body) + Cache-Control. A client that sends a matching
       If-None-Match gets 304 with no body: it already has this exact version.
    """
    key = await cache.key(namespace, request.url.path, query or {})
    payload = await cache.get(key)
    hit = payload is not None
    if not hit:
        payload = (await compute()).model_dump(mode="json")
        await cache.set(key, namespace, payload)

    body = JSONResponse(payload).body
    etag = etag_for(bytes(body))
    headers = {
        "ETag": etag,
        "Cache-Control": f"public, max-age={min(TTL[namespace], 300)}",
        "X-Cache": "HIT" if hit else "MISS",
    }
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)


def no_store(response: Response) -> None:
    """Per-user / auth responses must never be cached by browsers or proxies."""
    response.headers["Cache-Control"] = "private, no-store"
