# Multi-stage image for the API, Celery worker and beat.
#   target "prod": runtime deps only
#   target "dev":  + pytest/ruff/mypy, used by docker compose and `make test` / `make lint`
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    # venv lives outside /app so a dev bind-mount of the source does not hide it
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# ---------------------------------------------------------------- prod
FROM base AS prod
COPY pyproject.toml uv.lock ./
# Install dependencies first (cached layer), then the project itself.
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
RUN useradd --create-home --uid 1000 app
USER app
EXPOSE 8000
CMD ["uvicorn", "marketlens.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ---------------------------------------------------------------- dev
FROM base AS dev
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
COPY . .
RUN uv sync --frozen
EXPOSE 8000
CMD ["uvicorn", "marketlens.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
