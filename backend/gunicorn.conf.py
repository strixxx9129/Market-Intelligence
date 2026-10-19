"""Gunicorn settings for the production API (`gunicorn -c gunicorn.conf.py marketlens.main:app`).

Why Gunicorn in front of Uvicorn: Uvicorn is the async server inside each worker process;
Gunicorn is the process manager around them. It starts N workers (one Python process can only
use one CPU core), restarts a worker that crashes or hangs, recycles workers to cap slow memory
growth, and drains connections gracefully on deploy (SIGTERM → finish in-flight requests).
"""

import multiprocessing
import os
import shutil
from pathlib import Path
from typing import Any

bind = f"0.0.0.0:{os.environ.get('PORT', '8000')}"
worker_class = "uvicorn_worker.UvicornWorker"

# WEB_CONCURRENCY is the de-facto standard env var (Heroku, Render, Fly guides). Default:
# 2 per CPU, capped at 4 (each worker holds its own DB pool: workers x (pool + overflow) must stay
# below the Postgres connection limit).
workers = int(os.environ.get("WEB_CONCURRENCY", min(2 * multiprocessing.cpu_count(), 4)))

# Recycle each worker after ~1000 requests (jitter: not all at once) to bound memory creep.
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", 1000))
max_requests_jitter = 100

timeout = 30  # a worker silent for 30 s is killed and replaced (requests are ~ms)
graceful_timeout = 20  # on SIGTERM, wait up to 20 s for in-flight requests
keepalive = 5  # > 0: the platform proxy reuses connections

# Client IPs come from the platform's own header (ClientIPMiddleware, TRUSTED_CLIENT_IP_HEADER),
# never from X-Forwarded-For, which clients can forge. So trust no forwarders here.
forwarded_allow_ips = "127.0.0.1"

accesslog = None  # structlog in the app already logs requests as JSON
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info").lower()

# Prometheus multiprocess mode: every worker writes samples to this directory; /metrics
# aggregates them (otherwise each scrape would only see the one worker that answered it).
_PROM_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR")


def on_starting(server: Any) -> None:
    if _PROM_DIR:
        shutil.rmtree(_PROM_DIR, ignore_errors=True)
        Path(_PROM_DIR).mkdir(parents=True, exist_ok=True)


def when_ready(server: Any) -> None:
    """Serve the aggregated metrics on a PRIVATE port (METRICS_PORT, e.g. 9091). The platform
    scrapes it over the private network; it's never routed through the public proxy, so
    request counts and internals aren't readable by visitors."""
    port = os.environ.get("METRICS_PORT")
    if _PROM_DIR and port:
        from prometheus_client import CollectorRegistry, multiprocess, start_http_server

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
        start_http_server(int(port), registry=registry)


def child_exit(server: Any, worker: Any) -> None:
    if _PROM_DIR:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)  # type: ignore[no-untyped-call]
