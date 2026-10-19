# Multi-stage image for the API, Celery workers and beat.
#   target "prod": the production image (runtime deps only, non-root, no build tools, no uv)
#   target "dev":  + pytest/ruff/mypy, used by docker compose and `make test` / `make lint`
#   target "eda":  + Jupyter and data-science libs (notebooks/)

# ---------------------------------------------------------------- base (dev/eda tooling)
FROM python:3.14-slim AS base

# libgomp1: OpenMP runtime required by LightGBM.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # venv lives outside /app so a dev bind-mount of the source does not hide it
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# ---------------------------------------------------------------- builder (prod venv)
FROM base AS builder
COPY pyproject.toml uv.lock ./
# Dependencies first: this layer is cached until uv.lock changes.
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
# --no-editable: the package is COPIED into the venv, so the runtime image needs no source tree.
RUN uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------- prod
FROM python:3.14-slim AS prod

# libgomp1 for LightGBM; tini as PID 1 (forwards SIGTERM so Gunicorn/Celery shut down
# gracefully, and reaps zombie child processes). Nothing else: no compilers, no uv, no curl.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 tini \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y pip \
    && useradd --create-home --uid 1000 app \
    && mkdir -p /data/raw /tmp/prometheus \
    && chown -R app:app /data /tmp/prometheus

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    ENVIRONMENT=prod \
    RAW_DATA_DIR=/data/raw \
    PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus

COPY --from=builder /opt/venv /opt/venv
WORKDIR /app
# Only what runs: migrations (release step) and the Gunicorn config.
COPY alembic.ini gunicorn.conf.py ./
COPY alembic ./alembic

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"]
ENTRYPOINT ["tini", "--"]
# The same image runs every process; the platform overrides CMD per process group:
#   celery -A marketlens.tasks.celery_app worker --queues=... | beat | alembic upgrade head
CMD ["gunicorn", "-c", "gunicorn.conf.py", "marketlens.main:app"]

# ---------------------------------------------------------------- dev
FROM base AS dev
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
COPY . .
RUN uv sync --frozen
EXPOSE 8000
CMD ["uvicorn", "marketlens.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------- eda
# Jupyter Lab + data-science libs for notebooks/ and scripts/ (Phase 2 exploration).
# The repo root is bind-mounted at /workspace by the `notebook` compose service.
FROM dev AS eda
RUN uv sync --frozen --group eda
WORKDIR /workspace
EXPOSE 8888
CMD ["jupyter", "lab", "--ip=0.0.0.0", "--port=8888", "--no-browser", "--allow-root", \
     "--IdentityProvider.token=", "--ServerApp.root_dir=/workspace"]
