"""Request-scoped middleware: request IDs and verified caller identity.

The API is STATELESS: nothing about a caller is kept in process memory between requests.
Identity comes from the request itself (a JWT verified with the shared secret, or an API key
checked against the database), so any replica can serve any request and replicas can be added
or killed freely.
"""

import uuid
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import Request
from sqlalchemy import select, update
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from marketlens.api.ratelimit import current_identity
from marketlens.core.security import (
    InvalidTokenError,
    constant_time_equals,
    decode_access_token,
    parse_api_key,
    sha256_hex,
)
from marketlens.models import ApiKey, User

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if 0 < len(incoming) <= 128 else uuid.uuid4().hex
        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


class IdentityMiddleware(BaseHTTPMiddleware):
    """Resolve `request.state.identity` for rate limiting and auth dependencies.

    Invalid credentials are NOT rejected here (the endpoint's dependency does that with a clean
    401); they simply don't earn a better rate-limit bucket.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request.state.identity = None
        request.state.api_key_user = None
        api_key = request.headers.get("X-API-Key")
        auth = request.headers.get("Authorization", "")
        if api_key:
            await self._resolve_api_key(request, api_key)
        elif auth.lower().startswith("bearer "):
            try:
                claims = decode_access_token(auth[7:].strip())
                request.state.identity = f"user:{claims.user_id}"
            except InvalidTokenError:
                pass
        token = current_identity.set(request.state.identity)
        try:
            return await call_next(request)
        finally:
            current_identity.reset(token)

    async def _resolve_api_key(self, request: Request, key: str) -> None:
        prefix = parse_api_key(key)
        if prefix is None:
            return
        async with request.app.state.sessionmaker() as s:
            row = (
                await s.execute(
                    select(ApiKey, User)
                    .join(User, User.id == ApiKey.user_id)
                    .where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
                )
            ).first()
            if row is None or not constant_time_equals(row.ApiKey.key_hash, sha256_hex(key)):
                return
            if not row.User.is_active:
                return
            request.state.identity = f"apikey:{row.ApiKey.id}"
            request.state.api_key_user = row.User
            now = datetime.now(UTC)
            last = row.ApiKey.last_used_at
            if last is None or now - last > timedelta(hours=1):  # throttled bookkeeping write
                await s.execute(
                    update(ApiKey).where(ApiKey.id == row.ApiKey.id).values(last_used_at=now)
                )
                await s.commit()


class RateLimitHeadersMiddleware(BaseHTTPMiddleware):
    """Add X-RateLimit-Limit / -Remaining / -Reset to every rate-limited response, including
    responses returned directly (cached JSON), which a dependency can't decorate."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        view_limit = getattr(request.state, "view_rate_limit", None)
        if view_limit is not None and "X-RateLimit-Limit" not in response.headers:
            response = request.app.state.limiter._inject_headers(response, view_limit)
        return response
