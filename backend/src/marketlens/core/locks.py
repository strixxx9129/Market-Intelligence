"""A simple distributed lock on Redis: one holder at a time, across all workers and hosts.

    acquire:  SET <key> <token> NX EX <ttl>   (only if absent; auto-expires after ttl)
    release:  delete <key> ONLY if it still holds OUR token (atomic Lua script)

Why each part matters:
- NX: two workers can't both get the lock (Redis executes commands one at a time).
- EX/TTL: if a worker dies holding the lock, it frees itself instead of blocking forever.
  Choose a TTL longer than the job can take, otherwise a slow job loses its lock mid-run.
- token + compare-and-delete: if our lock already expired and someone else took it, a plain
  DEL would delete THEIR lock. The check and the delete must happen atomically, hence Lua.

This is the single-Redis-instance pattern (fine here). Locks guard against duplicate work;
correctness still relies on idempotent writes, because no lock is perfect.
"""

import uuid
from types import TracebackType

import redis

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class LockNotAcquiredError(Exception):
    pass


class RedisLock:
    def __init__(self, client: redis.Redis, key: str, ttl_seconds: int) -> None:
        self.client = client
        self.key = key
        self.ttl = ttl_seconds
        self.token = uuid.uuid4().hex
        self.acquired = False

    def acquire(self) -> bool:
        self.acquired = bool(self.client.set(self.key, self.token, nx=True, ex=self.ttl))
        return self.acquired

    def release(self) -> bool:
        if not self.acquired:
            return False
        self.acquired = False
        return bool(self.client.eval(_RELEASE, 1, self.key, self.token))

    def holder(self) -> str | None:
        value = self.client.get(self.key)
        return value.decode() if isinstance(value, bytes) else value

    def __enter__(self) -> "RedisLock":
        if not self.acquire():
            raise LockNotAcquiredError(self.key)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
