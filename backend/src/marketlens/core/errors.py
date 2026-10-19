"""Error classification for retries.

TRANSIENT errors may succeed if we simply try again later (network blips, a database that is
restarting, an S3 endpoint timing out). Celery retries those with exponential backoff.

Everything else is PERMANENT for retry purposes: bad data, a failed validation, a bug. Retrying
would fail the same way five more times and only delay the alert, so those fail immediately.
"""

import asyncpg
import botocore.exceptions
import redis.exceptions
import requests.exceptions
import sqlalchemy.exc


class TransientError(Exception):
    """Raise (or wrap) to say: this might work if retried later."""


class PermanentError(Exception):
    """Raise to say: retrying won't help (e.g. data failed validation). Stops the chain."""


TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TransientError,
    ConnectionError,  # builtin: refused/reset/aborted
    TimeoutError,
    sqlalchemy.exc.OperationalError,
    sqlalchemy.exc.InterfaceError,
    asyncpg.exceptions.PostgresConnectionError,
    asyncpg.exceptions.CannotConnectNowError,
    redis.exceptions.ConnectionError,
    redis.exceptions.TimeoutError,
    botocore.exceptions.EndpointConnectionError,
    botocore.exceptions.ConnectTimeoutError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
)


def is_transient(exc: BaseException) -> bool:
    return isinstance(exc, TRANSIENT_EXCEPTIONS)
