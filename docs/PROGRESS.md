# MarketLens: Progress

## Phase 1: Bootstrap the monorepo ✅ (2026-10-18)

**Goal:** a fresh clone runs with `cp .env.example .env && make up && make migrate && make test`.

### Built
- Monorepo layout (`backend/`, `frontend/`, `infra/`, `docs/`, `scripts/`, `notebooks/`, `.github/`).
- `backend/`: uv project, src layout, package `marketlens`, locked deps (`uv.lock`),
  multi-stage Dockerfile (`prod` / `dev` targets).
- Core: `core/config.py` (pydantic-settings, env-only config), `core/logging.py` (structlog,
  JSON or console, stdlib/uvicorn routed through it).
- DB: `db/base.py` (DeclarativeBase + constraint naming convention + timestamp mixin),
  `db/session.py` (async engine/sessionmaker created in app lifespan, `get_session` dependency),
  `db/seed.py` (idempotent BTC-USD asset UPSERT).
- Models + first Alembic migration (async `env.py`):
  - `assets` (natural key `symbol`)
  - `market_prices` (NUMERIC(20,8) OHLC, NUMERIC(30,8) volume, timestamptz `ts` = candle open,
    `UNIQUE(asset_id, interval, ts)`, CHECKs for interval, high ≥ low, non-negative)
  - `pipeline_runs` (UUID, status enum, `input_hash`, JSONB params)
- API: app factory, CORS, `/healthz` (liveness), `/readyz` (Postgres + Redis with timeout → 503),
  both at root and under `/api/v1`.
- Celery app (`tasks/celery_app.py`) with a `ping` task; worker + beat run as real processes.
- `infra/docker-compose.yml`: postgres 16, redis 7, SeaweedFS (S3), storage-init, MLflow
  (Postgres metadata + S3 artifacts), api, worker, beat. Healthchecks, named volumes,
  `depends_on` conditions.
- Makefile (`up down migrate test lint format seed logs build help`), all running in containers.
- `.pre-commit-config.yaml`, GitHub Actions CI (ruff, ruff format, mypy strict, pytest against
  Postgres + Redis service containers).
- README, LICENSE (Apache-2.0), CONTRIBUTING, ADR 0001, ADR 0002.
- Tests (15): healthz, readyz ok/503/timeout, config parsing, unique-constraint rejection,
  NUMERIC/UTC round-trip, CHECK constraint, seed idempotency, real-infra readyz. Integration tests
  migrate the test DB `downgrade base → upgrade head` so migrations are exercised both ways.

### Decisions
- Host needs only Docker + make; lint/test run inside the `dev` image (`UV_PROJECT_ENVIRONMENT=/opt/venv`
  so the source bind-mount doesn't hide the venv).
- **MinIO replaced by SeaweedFS** (MinIO images no longer public) → ADR 0002.
- Prices stored as NUMERIC, not float; `ts` is candle open time in UTC.
- Engine + Redis client live on `app.state`, created in lifespan (event-loop safe, clean shutdown).
- Integration tests use a separate `marketlens_test` DB with per-test transaction rollback.

### Known issues / notes
- `infra/postgres/init.sql` only runs on an empty volume. If you created the postgres volume
  before this file existed, run `make down` then `docker volume rm marketlens_postgres_data`.
- CI has not run on GitHub yet (no remote configured).
- `frontend/` is an empty placeholder.

## Next: Phase 2: Bitcoin data ingestion
Connector plugin registry, first BTC daily OHLCV connector (licence-checked source), raw file
hashing → `pipeline_runs.input_hash`, Pandera validation, UPSERT into `market_prices`,
Celery Beat schedule, read-only prices endpoint.
