"""Prometheus metrics for the API, and optional Sentry error tracking.

HTTP metrics use the ROUTE TEMPLATE as a label (`/api/v1/assets/{symbol}/prices`), never the
raw path: every distinct label value is a separate time series, and raw paths (one per
symbol, id or scanner probe) would grow without bound until Prometheus runs out of memory.
"""

import os
import time

from fastapi import Request
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
    multiprocess,
)
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import Scope

from marketlens.core.config import get_settings
from marketlens.core.logging import get_logger

logger = get_logger(__name__)

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)


def route_template(scope: Scope) -> str:
    """Full route template, e.g. `/api/v1/assets/{symbol}/prices`.

    FastAPI 0.146 keeps included routers nested, so `scope["route"].path` is only the INNER
    template (`/assets/{symbol}/prices`). The concrete path ends with that template filled in;
    what comes before it is the static include prefix (`/api/v1`), which we put back.
    """
    route = scope.get("route")
    inner = getattr(route, "path_format", None) or getattr(route, "path", None)
    if not inner:
        return "unmatched"
    params = {k: str(v) for k, v in scope.get("path_params", {}).items()}
    try:
        suffix = inner.format(**params)
    except (KeyError, IndexError, ValueError):
        return str(inner)
    path: str = scope.get("path", "")
    return path[: len(path) - len(suffix)] + inner if path.endswith(suffix) else str(inner)


class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.url.path == "/metrics":
            return await call_next(request)
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            template = route_template(request.scope)
            HTTP_LATENCY.labels(request.method, template).observe(time.perf_counter() - start)
            HTTP_REQUESTS.labels(request.method, template, str(status)).inc()


def metrics_response() -> Response:
    """Under Gunicorn (PROMETHEUS_MULTIPROC_DIR set), merge every worker's samples; otherwise
    a scrape would only see the one worker process that happened to answer it."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def init_sentry(component: str) -> bool:
    """Enable Sentry only when SENTRY_DSN is set. No personal data is sent."""
    s = get_settings()
    if not s.sentry_dsn:
        return False
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.fastapi import FastApiIntegration

    sentry_sdk.init(
        dsn=s.sentry_dsn,
        environment=s.environment,
        release=s.git_sha,
        traces_sample_rate=s.sentry_traces_sample_rate,
        send_default_pii=False,
        integrations=[FastApiIntegration(), CeleryIntegration()],
    )
    sentry_sdk.set_tag("component", component)
    logger.info("sentry_enabled", component=component)
    return True
