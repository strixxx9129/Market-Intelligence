"""Authentication: register, login, refresh (rotating), logout. Strictly rate limited per IP."""

from fastapi import APIRouter, Request, Response, status

from marketlens.api.deps import Session, no_store
from marketlens.api.ratelimit import ip_key, limiter
from marketlens.core.config import get_settings
from marketlens.schemas.auth import LoginIn, RefreshIn, RegisterIn, TokenPair, UserOut
from marketlens.schemas.common import ERROR_RESPONSES
from marketlens.services import auth

router = APIRouter(prefix="/auth", tags=["auth"], responses=ERROR_RESPONSES)
AUTH_LIMIT = get_settings().rate_limit_auth


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(AUTH_LIMIT, key_func=ip_key)
async def register(
    request: Request, response: Response, body: RegisterIn, session: Session
) -> UserOut:
    """Create an account (role `user`). Passwords: at least 12 characters, stored with Argon2id."""
    no_store(response)
    user = await auth.register(session, body.email, body.password)
    await session.commit()
    return UserOut(id=user.id, email=user.email, role=user.role, created_at=user.created_at)


@router.post("/login", response_model=TokenPair)
@limiter.limit(AUTH_LIMIT, key_func=ip_key)
async def login(request: Request, response: Response, body: LoginIn, session: Session) -> TokenPair:
    """Exchange credentials for a 15-minute access token and a single-use refresh token."""
    no_store(response)
    tokens = await auth.login(session, body.email, body.password)
    await session.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
@limiter.limit(AUTH_LIMIT, key_func=ip_key)
async def refresh(
    request: Request, response: Response, body: RefreshIn, session: Session
) -> TokenPair:
    """Rotate: the presented refresh token is consumed and a new pair is returned.
    Presenting an already-used token revokes the whole session (suspected theft)."""
    no_store(response)
    tokens = await auth.refresh(session, body.refresh_token)
    await session.commit()
    return tokens


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshIn, session: Session) -> Response:
    """Revoke the session (refresh-token family). Access tokens expire on their own (≤ 15 min)."""
    await auth.logout(session, body.refresh_token)
    await session.commit()
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})
