"""Passwords, access tokens (JWT), refresh tokens and API keys.

- Passwords → Argon2id (slow, memory-hard). Verification is constant-time inside argon2.
- Access token → short-lived JWT (15 min) signed with HS256. Stateless: any API replica can
  verify it without a database lookup. Short lifetime limits the damage of a leaked token.
- Refresh token → long-lived OPAQUE random string; only its SHA-256 is stored. Rotated on every
  use (see services/auth.py). Opaque, because it must be revocable server-side.
- API key → `ml_<prefix>_<secret>`; the prefix identifies the key, SHA-256(full key) verifies it.
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from marketlens.core.config import get_settings

_hasher = PasswordHasher()  # Argon2id with library defaults (RFC 9106 recommendations)
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------- access tokens (JWT)
@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    role: str
    jti: str


def create_access_token(user_id: uuid.UUID, role: str, now: datetime | None = None) -> str:
    s = get_settings()
    now = now or datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "role": role,
        "iss": s.jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=s.access_token_ttl_s)).timestamp()),
        "jti": uuid.uuid4().hex,
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=ALGORITHM)


class InvalidTokenError(Exception):
    pass


def decode_access_token(token: str) -> AccessClaims:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.jwt_secret,
            algorithms=[ALGORITHM],  # never let the token choose its own algorithm
            issuer=s.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise InvalidTokenError(str(exc)) from exc
    if payload.get("typ") != "access":
        raise InvalidTokenError("not an access token")
    return AccessClaims(uuid.UUID(payload["sub"]), str(payload["role"]), str(payload["jti"]))


# ---------------------------------------------------------------- opaque secrets
def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def new_api_key() -> tuple[str, str]:
    """Return (full_key, prefix). The full key is shown to the user exactly once."""
    prefix = secrets.token_hex(4)
    return f"ml_{prefix}_{secrets.token_urlsafe(32)}", prefix


def parse_api_key(key: str) -> str | None:
    """Return the prefix of a well-formed key, else None."""
    parts = key.split("_", 2)
    if len(parts) != 3 or parts[0] != "ml" or len(parts[1]) != 8:
        return None
    return parts[1]


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
