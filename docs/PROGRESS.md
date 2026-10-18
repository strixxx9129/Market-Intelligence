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

## Phase 3: Ingestion pipeline ✅ (2026-10-18)

**Goal:** raw CSV → validated, deduplicated daily candles in PostgreSQL, safe to re-run.

### Built
- Migration `e83bde7e29b5`: `raw_ingestions` (UNIQUE source+file_hash), `data_quality_issues`
  (UNIQUE asset_id+interval+ts+check_name, NULLS NOT DISTINCT; severity enum; JSONB details),
  `skipped` value for `pipeline_run_status` (downgrade recreates the enum).
- `ingestion/connectors/base.py`: `BaseConnector` (name, supported_intervals, supported_assets,
  `fetch()`, optional `fingerprint()`), registry with `@register_connector` + entry-point group
  `marketlens.connectors`.
- `ingestion/connectors/kaggle_btc.py`: chunked string-typed CSV read, minute-level checks
  (invalid timestamp, duplicate, missing/garbled value, non-positive price, negative volume,
  OHLC, malformed line), streaming per-day partial aggregation, incomplete-last-day withholding.
- `ingestion/validation.py`: Pandera schema (errors → quarantine) + warnings (zero volume,
  low coverage < 60 traded minutes, |return| > 50%, missing days); row-count invariant.
- `repositories/price_repo.py` (chunked UPSERT, 5,000 rows, `IS DISTINCT FROM` guard,
  float → Decimal(8)), `repositories/ingestion_repo.py` (runs, ledger, issues).
- `ingestion/pipeline.py`: `IngestionPipeline` service (run row, hash skip, full/incremental,
  atomic load, failure recording). `cli.py` (typer): `marketlens ingest | seed-assets |
  connectors`. `make ingest` (MODE=full|incremental, FORCE=1), `make seed` uses the CLI.
- Compose: backend services mount `../data:/data:ro`, `RAW_DATA_DIR=/data/raw`.
- Fixture generator + `tests/fixtures/kaggle_btc_sample.csv` (3 days + partial day, 7 kinds of
  broken rows, zero-volume fills, an extreme day). Tests: 47 total (32 new).
- ADR 0004; CONTRIBUTING connector guide.

### Real-data result (`make ingest MODE=full`, ~1.5 min)
7,781,879 raw rows → 5,404 daily candles, 0 quarantined, 201 warnings (197 low_coverage
2012–2015, 3 zero_volume 2015-01-06..08, 1 incomplete_period). Matches the Phase 2 EDA exactly.
Re-run → skipped by hash; `--force` re-run → 0 rows changed.

### Decisions
- ETL (aggregate + validate in Python, load daily rows); raw file + hash is the replay source.
- Quarantine, never delete; errors not loaded, warnings loaded and flagged.
- The incomplete last day is withheld and loaded by a later incremental run.
- `minutes_traded` is used for validation but not stored in `market_prices` (no schema change);
  low coverage is visible via `data_quality_issues`.
- New runtime deps: pandas, numpy, pandera (stack), typer (requested). mypy treats pandas as
  untyped (`pandas-stubs` would add stricter checking; not in the stack, not added).

### Known issues / notes
- pandas silently drops extra CSV fields when `usecols` is used; the connector deliberately
  reads all columns so malformed lines are reported (caught by the fixture tests).
- Changing validation rules requires `make ingest MODE=full FORCE=1` to re-evaluate history.

## Phase 4: Feature engineering ✅ (2026-10-19)

**Goal:** a versioned, leakage-free feature table built from a registry contributors can extend.

### Built
- Migration `987de3e24767`: `feature_sets` (version UNIQUE, definition JSONB, code_hash) and
  `features` (asset_id, ts, feature_set_id, values JSONB, UNIQUE asset_id+ts+feature_set_id).
- `features/registry.py`: `@register_feature(name, description, lookback)`, `FEATURES`,
  `FeatureSpec`, candle-contract check.
- 12 features in 6 families: returns (1/7/30d log returns), volatility (7d, 30d, ratio),
  momentum (close/SMA20−1, close/SMA50−1), range ((H−L)/C), volume (30d z-score of log1p
  volume vs previous 30 days, 1-day log1p change), calendar (day of week).
- `features/sets.py` (`v1`), `features/targets.py` (next-day log return, up label; never
  imported by features, never stored).
- `features/builder.py`: `FeatureBuilder` (compute → drop 49 warm-up rows → non-finite check →
  code-hash immutability check → UPSERT → pipeline_runs). `repositories/feature_repo.py`,
  `price_repo.load_candles()`.
- CLI `marketlens build-features --feature-set v1`, `marketlens features`; `make features`,
  `make feature-catalog`.
- `scripts/gen_feature_catalog.py` → `docs/feature-catalog.md`; CI `--check` step.
- Tests (112 total, 65 new): truncation test parametrised over all features with 3 leaky
  negative controls, static forward-looking-code guard, lookback honesty, hand-calculated
  values, targets, registry, builder integration (idempotent rebuild, +1 candle → +1 row,
  changed code refused, no targets stored).
- ADR 0005; CONTRIBUTING "Adding a feature".

### Real-data result
5,404 candles → 5,355 feature rows (2012-02-19 → 2026-10-17), code hash `92c21d11b1a2`;
rebuild → 0 rows changed. Leakage demo (naive next-day direction, 2013+): always-up 52.3%,
honest rules 48.0–51.3%, centred 5-day SMA 74.1%, `shift(-1)` feature 100%.

### Decisions
- Feature at row t uses rows ≤ t (close of day t included); target = t → t+1.
- Warm-up rows dropped, never filled; leftover NaN/inf fails the build.
- Version immutability via a hash of the set definition + whole feature-module source.
- Volume features use log1p(volume) (finite on the 2015 zero-volume days).
- Features stored from 2012-02-19; the 2013+ sample filter is applied at training time.
- No new dependencies.

### Known issues / notes
- Any edit to a feature module (even formatting) changes v1's code hash: the builder will refuse
  to rebuild v1 and CI flags the stale catalog. That's by design: add v2 or revert.
- Inside one DB transaction `now()` is constant (seen in tests); ordering runs by `started_at`
  is only meaningful across transactions.

## Next: Phase 5: Model training with walk-forward validation
Load features (v1, 2013+) + targets, walk-forward splits with a gap, sklearn Pipelines (scalers
fitted per fold), naive baselines (always-up, zero-return, last-sign), logistic regression and
LightGBM, metrics per fold/regime, MLflow + training_runs logging (dataset hash, feature-set
version + code hash, git SHA, params, seed).

## Later: Scheduling and read API
Celery Beat schedule for download + incremental ingest + feature build (worker tasks calling the
same services), read-only `/api/v1/assets`, `/api/v1/prices`, data-quality summary endpoints.
