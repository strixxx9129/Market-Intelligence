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

## Phase 2: Data exploration ✅ (2026-10-18)

**Goal:** understand the Bitcoin data before modelling it → [`docs/eda-report.md`](eda-report.md).

### Built
- `scripts/download_data.py` (`make data`): Kaggle API download of
  `mczielinski/bitcoin-historical-data` into gitignored `data/raw/`; prints size, SHA-256 and
  licence (CC-BY-SA-4.0); writes `data/raw/MANIFEST.json`; skips unchanged downloads.
- `notebooks/01_eda.py` (jupytext percent format; `make notebook` / `make eda`): hash check,
  chunked CSV load, UTC timestamps, quality report (duplicates, gaps, zero volume, OHLC
  violations, by year), daily resample with traded-minute coverage, simple/log returns,
  log-price + drawdown, return histogram vs normal/Student-t, 30-day rolling vol, ACF +
  Ljung-Box, yearly table, per-era robustness table, ADF tests.
- `data/interim/btc_daily.parquet` (gitignored): 5,404 complete UTC days with
  `minutes_traded` and `low_coverage`.
- `docs/eda-report.md` (9 findings) + `docs/figures/eda_*.png`.
- Tooling: `eda` dependency group, `eda` Dockerfile stage, `notebook` compose service (profile
  `eda`, localhost-only Jupyter), root `ruff.toml`; `make lint`, CI and pre-commit now also
  cover `scripts/` and `notebooks/`. → ADR 0003.

### Key findings (details in the report)
- Gaps are disguised as zero-volume flat-fill rows (16.9% overall, 95% in 2012) → modelling
  sample starts 2013-01-01; quality is measured in traded minutes.
- Price non-stationary (ADF p 0.75), log returns stationary (p ≈ 1e-24) → model returns.
- Fat tails (excess kurtosis 26, 18 days beyond 5σ) and negative skew (−1.3).
- Strong volatility clustering in every era; return autocorrelation weak and fading
  (Ljung-Box(10) p: 0.000 → 0.043 → 0.094 across eras).
- Clear regimes: annual vol 95% → 83% → 57% across eras.

### Decisions
- Modelling sample from 2013-01-01; UTC daily bars; unfinished current day always dropped.
- Targets from log returns; scale-free features only; walk-forward with gap; naive baselines
  required; metrics reported per fold/regime.
- New dependencies (eda group only): pandas, numpy, pyarrow, matplotlib, scipy, statsmodels,
  kaggle, jupyterlab, jupytext.

### Known issues / notes
- Docker Desktop VM has ~4 GB RAM; the running stack (MLflow ≈ 1.3 GB) plus the notebook is
  close to that limit. Chunked loading keeps the notebook within it.
- The Kaggle dataset is updated over time; `make data` then changes the SHA-256 and the
  numbers in the report. The report records the hash it was produced from.
- Derived figures/statistics are CC BY-SA 4.0 (dataset licence), attributed on every figure.

## Next: Phase 3: Bitcoin data ingestion
Connector plugin registry, first BTC daily OHLCV connector (licence-checked source), raw file
hashing → `pipeline_runs.input_hash`, Pandera validation reusing the Phase 2 quality checks
(OHLC consistency, duplicates, traded-minute coverage), UPSERT into `market_prices`,
Celery Beat schedule, read-only prices endpoint.
