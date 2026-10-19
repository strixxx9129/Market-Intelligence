"""Registration, login, refresh-token rotation, logout.

Refresh token rotation, step by step:
1. Login creates a token FAMILY (one per device/session) and returns its first refresh token.
2. Every /refresh consumes the presented token (marks it used) and returns a NEW one in the
   same family, plus a fresh 15-minute access token.
3. If a token that was already used is presented again, either the client is buggy or someone
   stole an old token and is replaying it. We can't tell which party is the thief, so the whole
   family is revoked: the attacker's copy dies, and the real user simply logs in again.
4. Logout revokes the family.

Access tokens are not revocable (stateless JWT); that's why they live only 15 minutes.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.config import get_settings
from marketlens.core.errors import ServiceError
from marketlens.core.security import (
    create_access_token,
    hash_password,
    new_refresh_token,
    sha256_hex,
    verify_password,
)
from marketlens.models import RefreshToken, Role, User
from marketlens.repositories import user_repo
from marketlens.schemas.auth import TokenPair

# Verifying against a real hash when the e-mail is unknown keeps login timing identical, so the
# response time doesn't reveal which e-mails have accounts.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")
_BAD_LOGIN = ServiceError(401, "invalid e-mail or password", "invalid_credentials")


async def register(session: AsyncSession, email: str, password: str) -> User:
    user = User(email=email, password_hash=hash_password(password), role=Role.USER)
    try:
        await user_repo.add(session, user)
    except IntegrityError as exc:
        raise ServiceError(
            409, "an account with this e-mail already exists", "email_taken"
        ) from exc
    return user


async def _issue(session: AsyncSession, user: User, family_id: uuid.UUID) -> TokenPair:
    s = get_settings()
    refresh = new_refresh_token()
    await user_repo.add(
        session,
        RefreshToken(
            user_id=user.id,
            family_id=family_id,
            token_hash=sha256_hex(refresh),
            expires_at=datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_s),
        ),
    )
    return TokenPair(
        access_token=create_access_token(user.id, user.role.value),
        expires_in=s.access_token_ttl_s,
        refresh_token=refresh,
    )


async def login(session: AsyncSession, email: str, password: str) -> TokenPair:
    user = await user_repo.by_email(session, email)
    if user is None:
        verify_password(_DUMMY_HASH, password)
        raise _BAD_LOGIN
    if not verify_password(user.password_hash, password) or not user.is_active:
        raise _BAD_LOGIN
    return await _issue(session, user, family_id=uuid.uuid4())


async def refresh(session: AsyncSession, token: str) -> TokenPair:
    now = datetime.now(UTC)
    row = await user_repo.refresh_by_hash(session, sha256_hex(token))  # row-locked
    invalid = ServiceError(401, "invalid refresh token", "invalid_refresh_token")
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise invalid
    if row.used_at is not None:
        # Reuse of a rotated token = possible theft → kill the whole session family.
        await user_repo.revoke_family(session, row.family_id, now)
        await session.commit()  # persist the revocation even though we're about to fail
        raise ServiceError(
            401, "refresh token reuse detected; session revoked", "refresh_token_reused"
        )
    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise invalid
    await user_repo.mark_used(session, row.id, now)
    return await _issue(session, user, family_id=row.family_id)


async def logout(session: AsyncSession, token: str) -> None:
    row = await user_repo.refresh_by_hash(session, sha256_hex(token))
    if row is not None:
        await user_repo.revoke_family(session, row.family_id, datetime.now(UTC))
