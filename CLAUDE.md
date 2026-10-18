# CLAUDE.md — MarketLens project rules

## What this project is
MarketLens is an open-source Market Intelligence & ML Monitoring Platform. It ingests historical
Bitcoin (later stocks) data, builds leakage-free features, trains and compares ML models with
walk-forward validation, backtests strategies after costs, monitors data/model health, and serves
everything through a public FastAPI API and a React website. Anyone can contribute connectors,
features and models through plugin registries.

I am an experienced Python backend developer (FastAPI, PostgreSQL, Redis, scheduled jobs) and I am
LEARNING data science and ML through this project. Teach as you build.

## Stack
- Backend: Python 3.12, uv, FastAPI, Pydantic v2, pydantic-settings, SQLAlchemy 2.0 (async) + asyncpg, Alembic
- Data/ML: pandas, numpy, Pandera, scikit-learn, LightGBM, MLflow, SeaweedFS (S3-compatible object storage; see docs/adr/0002)
- Jobs: Celery + Celery Beat, Redis (broker + cache)
- Frontend: React + TypeScript + Vite, Tailwind, TanStack Query, React Router, Lightweight Charts
- Quality: pytest, pytest-asyncio, httpx, Ruff, mypy (strict on src/), pre-commit, GitHub Actions
- Infra: Docker Compose (postgres:16, redis:7, seaweedfs, mlflow, api, worker, beat, frontend)

## Repo layout
backend/src/marketlens/{core, db, models, schemas, api/v1, services, repositories,
ingestion/connectors, features, ml/models, backtest, monitoring, tasks}
backend/tests/{unit, integration, fixtures}, backend/alembic/
frontend/, infra/, docs/, scripts/, notebooks/ (exploration only), .github/

Layering: routers (HTTP only) -> services (logic) -> repositories (SQL only).
Celery tasks call the same services. Never put business logic in routers or tasks.

## Non-negotiable rules
1. NO LOOK-AHEAD BIAS. A feature at day t uses only data <= t. Target = return from t to t+1.
   Splits are chronological (walk-forward with a gap). Never use random train/test splits.
   Scalers/encoders are fitted inside sklearn Pipelines on the training fold only.
2. IDEMPOTENCY. Every job can be re-run safely: UPSERT on natural keys
   (e.g. UNIQUE(asset_id, interval, ts)), file hashes for raw ingestions.
3. REPRODUCIBILITY. Every training run logs dataset version, feature_set version, git SHA,
   params, seed and metrics to MLflow and the training_runs table. Model versions are immutable.
4. ALL TIMESTAMPS UTC (timestamptz).
5. THE API NEVER TRAINS MODELS OR CALLS EXTERNAL DATA SOURCES during a request. It only reads
   precomputed data from PostgreSQL/Redis.
6. Educational software, not financial advice. Never present predictions as buy/sell advice.
   Every forecast response includes model_version, as_of_ts and a disclaimer.
7. Only redistribute data whose licence allows it.

## How to work with me
- Before writing code for a phase: read docs/PROGRESS.md, then show me a short plan
  (files to create/change, key design decisions) and wait for my OK.
- Work in small steps. After each step, run the relevant tests and fix failures before moving on.
- Explain new concepts briefly: WHAT it is, WHY we use it here, WHAT breaks without it.
- Type hints everywhere. Pydantic at API boundaries. Tests for every module.
- Ask before adding a dependency that is not in the stack above.
- Never commit secrets. Config comes from environment variables (.env, with .env.example committed).
- Done means: `make lint` and `make test` pass.

## Commands
make up | make down | make migrate | make test | make lint | make seed

## End of every phase
1. Update docs/PROGRESS.md: phase completed, what was built, decisions made, known issues, next phase.
2. Add an ADR in docs/adr/ for any significant design decision.
3. Suggest a Conventional Commit message (feat:, fix:, docs:, test:, chore:).