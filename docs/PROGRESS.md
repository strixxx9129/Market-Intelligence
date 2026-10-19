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

## Phase 5: Models and honest evaluation ✅ (2026-10-19)

**Goal:** compare baselines, linear/logistic, Random Forest and LightGBM on identical walk-forward
folds, logged to MLflow, with an experiment report → [`docs/experiment-report.md`](experiment-report.md).

### Built
- Migration `988db4803c01`: `training_runs`, `model_versions` (immutable: a trigger rejects any
  UPDATE except `stage`), `oos_predictions` (per-fold out-of-sample predictions for the backtest).
- `ml/splits.py`: `WalkForwardSplit` (expanding/rolling, embargo gap, sklearn splitter protocol),
  `inner_validation_split`.
- `ml/models/`: `BaseModel` + `@register_model` registry; baselines (zero return, historical
  mean, always-up, historical up-rate), Ridge, L2 Logistic (scaler + one-hot inside the
  pipeline), Random Forests (depth 4, leaf ≥ 50), LightGBM (7 leaves, depth 3) with an
  early-stopping wrapper that uses the most recent 15% of the training fold.
- `ml/evaluate.py`: MAE/RMSE/directional accuracy; log loss/Brier/ROC-AUC/accuracy; quantile
  calibration table; per-fold mean ± std; per-metric comparison with the best baseline (fold
  wins, "beats" = > 2 SE and majority of folds); per-era metrics; permutation importance with
  leak flags (dominant > 50% AND material ≥ 1% of the score).
- `ml/dataset.py` (features + targets, dataset hash), `ml/train.py` (`Trainer`), `ml/report.py`,
  `repositories/training_repo.py`. CLI `marketlens train`, `marketlens experiment-report`;
  `make train TASK=…`, `make report`.
- `notebooks/02_random_vs_walkforward.py` + `docs/figures/random_vs_walkforward.png`.
- Plumbing: `mlflow-skinny` (pinned to the server's 3.16), scikit-learn, LightGBM, joblib;
  `libgomp1` in the image; `docs/` mounted into backend containers; `GIT_SHA` (with `-dirty`)
  passed by the Makefile; `MLFLOW_TRACKING_URI`, `REPORTS_DIR`.
- Tests: 467 total (355 new, most of them split property cases): splits, models, metrics,
  calibration, importance/leak flags, per-fold scaler fitting, LightGBM early-stopping slice,
  random-split demo, end-to-end training with MLflow (file store), immutability trigger,
  versioning, report rendering. ADR 0006.

### Results (BTC-USD, v1, 2013-01-01 → 2026-10-16, 5,037 days, 24 folds)
- **Classification: no model beats the baseline.** Log loss: random forest 0.69251, historical
  up-rate 0.69272, logistic 0.69307, LightGBM 0.69499 (best model wins 10/24 folds; within
  noise). Always-up: accuracy ≈ up-rate but log loss 16.5 (certainty punished).
- **Regression: baselines win outright.** MAE: zero return 0.02293 < historical mean 0.02295 <
  LightGBM 0.02303 < RF 0.02306 < Ridge 0.02314.
- Calibration: the RF's extreme bins are over-confident (predicts 0.62, observes 0.55).
- No possible leak flagged; dominant features (`log_return_1d`, `close_to_sma_20`) hold most of
  effects worth ~0.2% of the score.
- Random vs walk-forward (real data, RF): 1-day target 52.2% vs 52.5% (no inflation, no skill);
  7-day overlapping target 58.6% shuffled vs 53.4% walk-forward (always-up 54.4%).
- Re-running with the same seed reproduced every metric exactly.

### Decisions
- Primary metrics: log loss (classification), MAE (regression); accuracy reported, never decisive.
- No hyperparameter tuning on reported folds; fixed conservative defaults.
- Gap 5 days (≥ 1-day label horizon + margin); expanding window default.
- The final model per version is fitted on all data; its quality estimate is the walk-forward OOS.
- Importance leak rule needs materiality: dominance alone fires on noise when models have no
  skill (seen on the first real run, fixed and tested).

### Known issues / notes
- A full training run with permutation importance takes ~6 min per task on the dev VM.
- JSONB doesn't preserve object key order; consumers of `metrics` must sort explicitly.
- MLflow 3 refuses the local file store unless `MLFLOW_ALLOW_FILE_STORE=true` (tests only).
- Model stages are all `candidate`; promotion logic comes later.

## Phase 6: Backtesting ✅ (2026-10-19)

**Goal:** turn OOS predictions into a hypothetical strategy and judge it after costs against
buy-and-hold and the baseline → [`docs/backtest-report.md`](backtest-report.md).

### Built
- Migration `72b1b61acb31`: `backtests` (model_version_id, baseline_model_version_id, config,
  metrics JSONB, equity_curve_uri, git_sha).
- `backtest/engine.py`: vectorised engine (signal rules long/flat and long/short, `next_open` /
  `next_close`, fee + slippage per unit traded, optional volatility targeting, trade list with
  per-trade costs). Position = previous decision day's target (`shift(1)`).
- `backtest/metrics.py`: CAGR, volatility (√365), Sharpe, Sortino, max drawdown + duration,
  turnover, hit rate, trade win rate, exposure, costs; yearly returns.
- `backtest/selection.py`: threshold grid chosen on the first half of folds by net Sharpe, prices
  cut at the selection end. `backtest/service.py`: `Backtester` (evaluation on later folds,
  buy-and-hold, baseline strategy, cash, timing + cost sensitivity, Sharpe SE).
- `core/storage.py`: `ObjectStore` with `S3ObjectStore` (boto3 → SeaweedFS) and
  `LocalObjectStore`; equity curves as Parquet at `s3://backtests/<id>.parquet`.
- CLI `marketlens backtest | backtest-report | model-versions`; `make backtest MV=…`,
  `make backtest-report`. ADR 0007.
- Infra: `backtests` bucket via `storage-init` (`BUCKETS`), SeaweedFS volume flags, S3 settings,
  pyarrow + boto3 runtime deps.
- Tests: always-long = buy-and-hold, no same-close fills, costs ∝ turnover (and 2× costs = 2×
  drag), flips = 2 units, hand-computed drawdown, vol targeting past-only, trades compound to
  equity, selection independent of evaluation prices, oracle positive control for timing,
  service integration (local store), report rendering.

### Results (evaluation folds 12–23: 2020-12-06 → 2026-10-16, 10 + 5 bps)
- Buy-and-hold: CAGR 28.0%, Sharpe 0.72 ± 0.46, max DD −76.7%. Classification baselines
  reproduce it exactly (always long); the regression baseline (`zero_return`) stays in cash.
- Only `lightgbm_clf` beats buy-and-hold on net Sharpe (0.92 ± 0.49; CAGR 38%, max DD −52.5%,
  70% exposure), a difference of 0.20, far inside the noise (2 SE ≈ 1.0).
- Costs flip conclusions: `logistic` gross Sharpe 1.18 → net 0.68 (145× turnover/year, costs
  ≈ 127% of capital over the period); at 30 bps it is flat, at 60 bps −36% CAGR.
- Timing: `logistic` CAGR +22% with `next_open`, −15% with `next_close`.
- Tuning optimism: selection-period Sharpe 1.40–1.44 → evaluation 0.55–0.92 for the models.

### Decisions
- Default execution `next_open` (earliest realistic fill for a 24/7 market) with `next_close`
  always reported as a sensitivity.
- Threshold selection on the first half of folds; never on the reported folds.
- "Beats buy-and-hold" in the report requires > 2 Sharpe standard errors.

### Known issues / notes
- SeaweedFS default 30 GB volumes left no room for a second bucket on the dev disk; fixed with
  `-master.volumeSizeLimitMB=1024 -volume.max=100` (ADR 0007).
- Shorts ignore borrow/funding costs; slippage is flat (no market impact model).
- Stored backtests carry `git_sha a5ed507-dirty` until the phase is committed.

## Phase 7: Production pipeline with Celery ✅ (2026-10-19)

**Goal:** the daily cycle runs by itself, survives failures, and failed runs can be retried safely.

### Built
- `tasks/celery_app.py`: Redis broker/backend, JSON only, `acks_late`,
  `task_reject_on_worker_lost`, prefetch 1, UTC, queues `ingest` / `features` / `train` /
  `predict` with routes. `tasks/schedules.py`: Beat 00:15 daily chain, 01:00 outcomes,
  Sun 02:00 weekly chain.
- `tasks/jobs.py`: `ingest` (Kaggle refresh + incremental ingest), `validate`, `build_features`,
  `predict`, `evaluate_outcomes`, `train_candidates`, `promote_model`, `backfill`;
  `daily_chain` / `weekly_chain` with immutable signatures.
- `tasks/reliable.py`: per-attempt `pipeline_runs` rows, Redis lock, transient-error autoretry
  (backoff + jitter, max 5), `retrying` status. `core/locks.py` (SET NX EX + Lua release),
  `core/errors.py` (transient vs permanent), `tasks/runtime.py` (per-task engine, test hooks).
- Services: `data_health` (validate), `prediction` (production model via MLflow artifact cache,
  UPSERT; outcomes), `promotion` (gate + baseline fallback + recorded decisions), `admin`
  (retry with original params). `ingestion/kaggle_download.py` (shared by the worker and
  `scripts/download_data.py`); connector `refresh()` hook.
- Migration `3c5d7e9a1b20`: `predictions`, `prediction_outcomes`, `promotion_decisions`;
  `pipeline_runs.attempt / celery_task_id / root_id / parent_run_id`; status `retrying`.
- Compose: `worker` (ingest, features, predict), `worker-train` (train, concurrency 1,
  max-tasks-per-child 1), `beat` (persistent schedule volume); Kaggle token + writable `data/`
  for workers only. CLI `marketlens admin runs|retry|backfill|run-daily|run-weekly`;
  `make daily|weekly|runs|retry|backfill`.
- Tests (533 total, 43 new): fail-twice-then-succeed (attempt rows retrying/retrying/success,
  same params), real in-process worker retry through a broker, give-up after 5 retries, permanent
  errors not retried, lock-held → skipped, two simultaneous runs → one executes, lock released only
  by owner, predict re-run → no duplicates, backfill idempotent and never overwrites live,
  outcomes scored once, validate stops the chain, admin retry, gate reject (+fallback) and
  promote; unit tests for gate, error classes, Celery config, routes, retry policy, schedule.
- ADR 0008.

### Live verification (real stack)
- Daily chain queued like Beat: the worker downloaded a NEW Kaggle dataset version, ingested
  1 new candle (2026-10-18), validate passed, features +1 row, predict wrote
  as_of 2026-10-18 → target 2026-10-19, P(up) 0.5231 by `historical_up_rate` v2 (baseline
  fallback, recorded in `promotion_decisions`).
- Two chains queued simultaneously: one stopped at `ingest` (lock held), the other skipped
  ingest by file hash and still completed. Still exactly 1 prediction.
- Weekly chain on the train worker: retrained on the new data, staged `random_forest_clf`, gate
  REJECTED it (mean improvement +0.00021 < 0.001; worse in 14/24 folds > 8). Production stays on
  the baseline.

### Decisions
- One `pipeline_runs` row per attempt; Celery's result backend is not a source of truth.
- Promotion gate defaults: improvement ≥ 0.001 mean fold log loss, ≤ 8 worse folds; baseline
  fallback when nothing qualifies.
- Backfilled forecasts are flagged `source='backfill'` (not out-of-sample).
- `kaggle` moved to runtime dependencies (workers download data).

### Known issues / notes
- Docker Desktop's WSL backend hung during development and needed a restart; nothing in the
  project depends on it beyond running the stack.
- In tests, eager Celery doesn't re-queue retries, so a `deliver()` helper emulates redelivery,
  plus one real-worker test. A blocking `result.get()` stalls with an in-memory backend shared
  across tests, so that test waits on `pipeline_runs` instead.
- Forecasts target the next UTC day; `evaluate_outcomes` scores them the morning after.

## Phase 8: Public API ✅ (2026-10-19)

**Goal:** a versioned, documented, cached, rate-limited API for the website and developers.

### Built
- Public (cached, rate limited): `GET /api/v1/assets`, `/assets/{symbol}/prices` (keyset
  cursor on ts, `from`/`to`), `/assets/{symbol}/features`, `/assets/{symbol}/forecast`
  (production model, `model_version`, `as_of_ts`, `target_ts`, disclaimer, 90-day live track
  record vs always-up and coin flip), `/models` (leaderboard), `/models/{id}` (model card),
  `/backtests/{id}`, `/status` (freshness, last run per job, derived system alerts).
- Auth: `users` (Argon2id), JWT access 15 min, rotating refresh tokens with reuse detection,
  roles user < contributor < maintainer < admin, `api_keys` (SHA-256 at rest, shown once);
  `POST /auth/register|login|refresh|logout`.
- `/me`, `/me/watchlists[...]`, `/me/alerts[...]` (stored rules), `/me/api-keys[...]`.
- Admin: `POST /admin/runs/{id}/retry` (enqueues), `POST /admin/models/{id}/promote` (gate;
  `force` + reason → audited `manual` decision). CLI `marketlens admin create-user|set-role`.
- Infrastructure: `core/security.py`, `core/cache.py` (versioned cache-aside + invalidation
  hooks in prediction/promotion services and ingest/feature tasks), `api/ratelimit.py`,
  `api/middleware.py` (request ID, verified identity, rate-limit headers), `api/errors.py`
  (single error format), `api/deps.py` (auth, roles, cached responses with ETag/304).
- Migration `cb001245dfaf`: users, refresh_tokens, api_keys, watchlists, watchlist_items, alerts;
  promotion_decisions accepts `manual`.
- `infra/loadtest/locustfile.py`, compose profile `loadtest` (`api-load` without limits +
  pinned `locustio/locust:2.42.6`), `make loadtest` / `make loadtest-ui`. ADR 0009.
- Tests: 560 total (27 new API tests): every router, cursor walk, error format, request ID,
  auth flow incl. refresh reuse revoking the family, expired/forged tokens, roles (maintainer
  can't admin), ownership isolation, API keys, admin retry/promote (gate, force audit),
  cache HIT without DB, ETag 304, invalidation on a new prediction, rate limits per IP / per
  key / fake keys / login throttle.

### Live checks
- Real data: assets, prices, forecast (baseline `historical_up_rate` v2, P(up) 0.5231 for
  2026-10-19, track record n=0), leaderboard, status.
- Rate limiting: 60 anonymous requests pass, then 429 with Retry-After and X-RateLimit headers.
- Locust, 50 users, 45 s: 2,072 requests, 0 failures, p50 7 ms, p95 17 ms (cache warm).
- `ENVIRONMENT=prod` with the default JWT secret refuses to start.

### Decisions
- API writes only its own operational data; admin actions enqueue or flip stages via the gate.
- Namespace-version cache invalidation; public data `Cache-Control: public`; `/me` no-store.
- Rate-limit identity verified before limiting; limits applied by a router dependency.
- Simple e-mail validation (no `email-validator` dependency); verification would be by e-mail.
- New deps: argon2-cffi, PyJWT, slowapi.

### Known issues / notes
- slowapi 0.1.10 + FastAPI 0.146: its middleware misses nested routers and fails open; worked
  around (ADR 0009) and guarded by tests.
- Rate-limit tests reset limiter counters in the dev Redis.
- User alerts are not evaluated or delivered yet.

## Phase 9: Monitoring ✅ (2026-10-19)

**Goal:** detect stale data, input drift and model degradation before users notice.

### Built
- Migration `49f34908b251`:
  - `monitoring_metrics` (metric_name, asset_id, model_version_id, ts, value, threshold,
    breached, details, pipeline_run_id), UNIQUE NULLS NOT DISTINCT on the natural key;
  - `monitoring_alerts` (dedup key, severity, status, delivery error).
- `monitoring/`:
  - `stats.py`: PSI with reference-fixed quantile bins, categorical PSI, KS, adaptive bins,
    noise floor, chi-squared critical value.
  - `freshness.py`: hours since the last candle closed.
  - `drift.py`: PSI + KS per feature vs the model's training window, and prediction drift on
    P(up) vs its walk-forward predictions.
  - `performance.py`: 30/90-day log loss, Brier and accuracy vs walk-forward, coin flip and
    always-up.
  - `alerts.py`: thresholds from config, metric UPSERT, Discord/Slack webhook, cooldown dedup.
  - `service.py`
- Repository `monitoring_repo.py`.
- Celery:
  - tasks `monitor_freshness|drift|performance` (queue `monitoring`, with retries, locks and
    `pipeline_runs`);
  - Beat schedule: hh:05, 00:45, 01:15.
- `/api/v1/status` includes monitoring alerts from the last 24 h.
- Observability:
  - `core/observability.py`: request count/latency by full route template, `/metrics`, and
    optional Sentry (`SENTRY_DSN`).
  - `tasks/metrics.py`: Celery task duration/outcome/retry counters in multiprocess mode, on
    :9808.
  - Prometheus v3.5.0 and Grafana 12.1.1 in compose, with provisioned datasources and the
    "MarketLens overview" dashboard (API, Celery, freshness, PSI, live log loss, alerts).
- Tests: 584 total. New:
  - PSI hand calculation, empty bins, constant reference, categorical PSI, KS;
  - noise floor, plus a false-alarm/power simulation;
  - webhook payload, route-template labels, Sentry off by default, routes and schedule;
  - chaos test: 2 days without ingestion plus a ×3 shift, asserting freshness and drift alerts
    are recorded, delivered once, suppressed inside the cooldown and re-sent after it, and
    shown on `/status`;
  - healthy-data control, failed webhook retried, live log-loss breach, insufficient labels.

### Live verification (real data, real worker)
- Freshness 15.4 h (ok).
- 7 PSI alerts, all on slow rolling features: `volatility_30d` 5.1, `log_return_30d` 5.1,
  `close_to_sma_50` 3.7, and others. The last 30 days are a calm regime compared with the
  2013–2026 training average. `log_return_1d` PSI 0.41 was correctly *not* alerted: not
  significant on 30 rows.
- Performance: insufficient data (0 labelled live forecasts).
- `psi:p_up`: only 1 live forecast so far.
- Prometheus: the api, worker and worker-train targets are up; Celery counters are aggregated
  across children.
- Grafana: dashboard and both datasources provisioned; every SQL panel returns rows.

### Decisions
- Drift alert = large (PSI > 0.25) AND significant (χ² reading of PSI or KS p < 0.01): textbook
  thresholds alone alerted on noise in more than 30% of 30-day windows (ADR 0010).
- Drift triggers investigation, not retraining. Labelled log loss is the degradation signal.
- Alert dedup and history live in Postgres (cooldown 24 h). Failed deliveries are retried on
  the next run.
- New dependencies: prometheus-client, sentry-sdk; scipy and httpx moved to runtime.

### Known issues / notes
- Slow features show permanent "major" drift against a 13-year training window. This is a
  regime difference, and it produces up to 7 warnings in one batch. Options: group alerts per
  model, or compare with a recent reference.
- The production model is a baseline that ignores features, so feature drift can't hurt its
  forecasts today.
- Live performance needs ≥ 20 labelled days before it says anything.
- `monitoring_metrics` has no retention policy yet. Grafana uses the app DB user (use a
  read-only role in production).
- Memory: the full stack (Prometheus, Grafana, MLflow ~1.3 GB, two workers) plus `make test`
  can exhaust a 4 GB Docker VM (seen as `ENOMEM` or DNS errors, and a hung test). Stop
  grafana/prometheus/worker-train before running tests, or give Docker more memory.

## Phase 10: Website ✅ (2026-10-19)

**Goal:** a fast, clear, mobile-friendly website a non-developer can use.

### Built
- `frontend/`:
  - Vite 7, React 19, TypeScript strict, Tailwind 4, React Router 7, TanStack Query 5,
    Lightweight Charts 5, pnpm 10 (pinned, lockfile).
  - ESLint 9 + Prettier; Vitest 3 + Testing Library.
- Typed API client:
  - `make openapi` (`scripts/export_openapi.py`) writes `frontend/openapi.json`;
  - `pnpm gen:api` (openapi-typescript) writes `src/api/schema.d.ts`;
  - `openapi-fetch` client with an auth middleware (bearer token, one shared refresh on 401,
    replay of the original request).
- Pages, each a lazily loaded chunk:
  - Home: asset cards with price, 1-day change and a forecast summary;
  - Asset: candles with 1M/3M/1Y/5Y/All, daily return and volatility charts from the
    features, forecast panel;
  - Models: leaderboard with a verdict and per-fold bars vs the baseline;
  - Model card: metrics, training provenance, calibration, permutation importance, promotions,
    backtests;
  - Backtests list and detail: equity curves before/after costs vs buy-and-hold and the
    baseline, results table, every assumption, year by year;
  - Status: alerts, freshness, runs; refreshes every minute;
  - Login / Register, Account (watchlists, alert rules marked "not delivered yet", API keys
    shown once), Contribute (guides plus GitHub issue links from `VITE_GITHUB_URL`).
- Layout: responsive nav with a mobile menu; dark mode (system or remembered, no flash);
  skeletons, error states with retry, empty states; up/down always as an arrow plus a word;
  disclaimer on every forecast surface.
- Backend additions (read-only, Postgres only):
  - `AssetOut.prev_close` / `change_1d_pct` (LATERAL LIMIT 2);
  - `GET /api/v1/backtests`;
  - `LeaderboardEntry.per_fold`;
  - `BacktestOut.equity_curve`, from migration `f3fa6aae83db` (`backtests.equity_curve`
    JSONB, weekly downsample written by the backtester); `marketlens backfill-equity-curves
    [--force]` filled the 10 existing backtests.
- Compose `frontend` service (Vite dev server, `127.0.0.1:5173`, named `node_modules`
  volume).
- Make targets: `make web`, `web-install`, `web-gen`, `web-test`, `web-build`; `make lint`
  and `make test` include the frontend.
- CI: new `frontend` job (frozen install, generated-client check, lint, typecheck, tests,
  build); the backend job checks the OpenAPI snapshot.
- Tests:
  - backend: 586 (new: weekly curve downsampling, 1-day change, backtest list, equity curve,
    per-fold);
  - frontend: 30.
    - ForecastPanel: probability, model version, as-of time, track record vs yardsticks,
      coin-flip wording, short track record, baseline notice, arrow plus word, no buy/sell
      wording, and the disclaimer in 6 states.
    - Auth: login stores tokens, bearer header, 401 → single refresh → replay (including a
      POST body), concurrent 401s share one refresh, a rejected refresh logs out, a failed
      login doesn't refresh, logout revokes.
    - Page-level flows: login → account, wrong password, protected-route redirect and back,
      session restore after reload, register.
    - Formatters.
- ADR 0011.

### Live verification (real stack, browser)
- Every page renders against real data.
- Asset "All" loads 5,405 candles through 6 cursor pages.
- Range switches and remounts produce no errors.
- Live auth in the browser: register → account → create watchlist → reload. On reload:
  `/me` 401, one `/auth/refresh`, `/me` 200 (with CORS preflights), and the refresh token
  rotated.
- At 375 px wide, light mode: no horizontal scroll; the menu collapses.
- Build: entry chunk 119 kB gzip, charts chunk 55 kB gzip (chart pages only), pages 0.3–7 kB
  each.
- The backtest page shows the cost lesson: ridge strategy 8.73× before costs, 2.48× after,
  buy-and-hold 4.25×.

### Decisions
- Generated client plus snapshot checks in CI on both sides (ADR 0011).
- Server state only in TanStack Query; UI state local.
- Access token in memory, refresh token in localStorage with rotation (follow-up: httpOnly
  cookie).
- Forecast wording: probabilities with yardsticks, "close to a coin flip" within ±5 points,
  never instructions.
- Weekly equity curves in Postgres for the website, daily Parquet in S3 for analysis.
- New dependencies: openapi-fetch, plus dev helpers (jsdom, jest-dom, user-event,
  typescript-eslint, react-hooks, react-refresh, eslint-config-prettier, globals).

### Known issues / notes
- React StrictMode surfaced a chart lifecycle bug: series were kept from a destroyed chart.
  Fixed: the chart instance is state, and series are tied to it.
- jsdom vs Node `AbortSignal` clash in tests (React Router navigation): the test setup drops
  the signal from `Request`.
- Test timeouts were raised (async 5 s, test 15 s): the Docker VM is CPU-starved while the
  full stack runs.
- The features chart covers 365 days (API maximum). Alert rules are stored, not evaluated.
- The GitHub URL is a placeholder until a remote exists (`VITE_GITHUB_URL`).
- Docker Desktop hung mid-phase (WSL backend). Frontend checks ran on host Node 24 in the
  meantime, then all were re-run in the container.

## Next: Phase 11 (to be defined)
Candidates:
- alert evaluation and delivery (rules are stored already);
- stock connectors (second asset class);
- production hardening: httpOnly refresh cookie, production build served by nginx/CDN, CSP
  headers, deploy pipeline.

## Later
- Alert evaluation task after `predict` + notification channel (e-mail/webhook).
- Auth hardening: refresh token in an httpOnly SameSite cookie (+ CSRF), CSP headers.
- Monitoring follow-ups: grouped alerts per model, metrics retention, Prometheus alert rules
  for API/worker SLOs.
