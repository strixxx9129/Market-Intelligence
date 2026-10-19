"""Liveness and readiness probes.

- /healthz (liveness): "is the process alive?" Touches no dependency, so a database outage
  never makes an orchestrator restart healthy API processes in a loop.
- /readyz (readiness): "can this instance serve traffic?" Checks PostgreSQL and Redis with a
  short timeout and returns 503 if any check fails, so load balancers stop routing to it.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from marketlens import __version__
from marketlens.core.config import get_settings
from marketlens.core.logging import get_logger
from marketlens.schemas.health import CheckStatus, HealthResponse, ReadinessResponse

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

Check = Callable[[], Awaitable[object]]


def get_readiness_checks(request: Request) -> dict[str, Check]:
    """Dependency returning named async checks. Overridden in tests to simulate outages."""
    engine: AsyncEngine = request.app.state.engine
    redis: Redis = request.app.state.redis

    async def database() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def cache() -> object:
        return await redis.ping()

    return {"database": database, "redis": cache}


async def _run_check(name: str, check: Check, timeout_s: float) -> CheckStatus:
    try:
        await asyncio.wait_for(check(), timeout=timeout_s)
    except Exception as exc:
        logger.warning("readiness_check_failed", check=name, error=repr(exc))
        return "error"
    return "ok"


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    return HealthResponse(version=__version__, git_sha=get_settings().git_sha)


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
)
async def readyz(
    response: Response,
    checks: Annotated[dict[str, Check], Depends(get_readiness_checks)],
) -> ReadinessResponse:
    timeout_s = get_settings().readiness_timeout_s
    names = list(checks)
    results = await asyncio.gather(*(_run_check(n, checks[n], timeout_s) for n in names))
    statuses: dict[str, CheckStatus] = dict(zip(names, results, strict=True))
    ok = all(s == "ok" for s in statuses.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="ok" if ok else "error", checks=statuses)
