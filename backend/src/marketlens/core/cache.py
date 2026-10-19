"""Redis cache-aside with namespace versions.

    read:   key = cache:<ns>:v<version>:<path>?<sorted query>
            hit  → return cached JSON
            miss → compute from PostgreSQL, store with the endpoint's TTL, return
    invalidate(ns):  INCR cache:<ns>:version

Invalidation never deletes keys. Bumping the version makes every old key unreachable at once
(they simply expire via their TTL). That's O(1), needs no SCAN over the keyspace, and can't
race with a reader that is just writing an old entry back: it writes under the OLD version.

The cache is an optimisation, never a dependency: if Redis is down, requests fall through to
the database.
"""

import hashlib
import json
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from marketlens.core.config import get_settings
from marketlens.core.logging import get_logger

logger = get_logger(__name__)

# Namespaces and their TTLs (seconds). Data changes daily; `forecast` is also invalidated
# explicitly whenever a new prediction is written.
TTL = {
    "assets": 3600,
    "prices": 600,
    "features": 600,
    "forecast": 3600,
    "models": 3600,
    "backtests": 3600,
    "status": 30,
}


def etag_for(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


class Cache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.enabled = get_settings().cache_enabled

    async def _version(self, ns: str) -> int:
        value = await self.redis.get(f"cache:{ns}:version")
        return int(value or 0)

    async def key(self, ns: str, path: str, query: dict[str, Any]) -> str:
        q = "&".join(f"{k}={query[k]}" for k in sorted(query) if query[k] is not None)
        return f"cache:{ns}:v{await self._version(ns)}:{path}?{q}"

    async def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        try:
            raw = await self.redis.get(key)
        except RedisError as exc:
            logger.warning("cache_unavailable", error=repr(exc))
            return None
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, ns: str, value: Any) -> None:
        if not self.enabled:
            return
        try:
            await self.redis.set(key, json.dumps(value, default=str), ex=TTL[ns])
        except RedisError as exc:
            logger.warning("cache_unavailable", error=repr(exc))


async def invalidate(redis: Redis, *namespaces: str) -> None:
    for ns in namespaces:
        await redis.incr(f"cache:{ns}:version")


async def invalidate_namespaces(*namespaces: str) -> None:
    """Best-effort invalidation from writers outside the API (Celery tasks, CLI).

    Never fails the caller: if Redis is unreachable, entries simply expire via their TTL.
    """
    client = Redis.from_url(get_settings().redis_url)
    try:
        await invalidate(client, *namespaces)
    except RedisError as exc:
        logger.warning("cache_invalidation_failed", namespaces=namespaces, error=repr(exc))
    finally:
        await client.aclose()
