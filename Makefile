# All targets run inside containers, so the only host requirements are Docker and make.
# Windows: `winget install ezwinports.make` (or run from Git Bash / WSL).

# Commit being run, recorded with every training run (reproducibility). "-dirty" = uncommitted changes.
export GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)$(shell git diff --quiet HEAD 2>/dev/null || echo -dirty)

COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
RUN     := $(COMPOSE) run --rm --no-deps api
EDA     := $(COMPOSE) --profile eda run --rm --no-deps notebook
WEB     := $(COMPOSE) run --rm --no-deps frontend sh -c

.DEFAULT_GOAL := help
.PHONY: changelog backup restore-drill help up down migrate test lint format seed ingest features feature-catalog train report backtest backtest-report daily weekly runs retry backfill loadtest loadtest-ui logs build data notebook eda openapi web web-install web-gen web-test web-build

help: ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

build: ## Build images
	$(COMPOSE) build

up: ## Start the whole stack in the background and wait until it is healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack (data volumes are kept; add `-v` manually to wipe them)
	$(COMPOSE) down

migrate: ## Apply database migrations (alembic upgrade head)
	$(COMPOSE) run --rm api alembic upgrade head

test: ## Run the backend test suite (needs `make up` for postgres/redis) and the frontend tests
	$(COMPOSE) run --rm api pytest
	$(WEB) "corepack enable && pnpm install --frozen-lockfile && pnpm test"

lint: ## ruff + mypy --strict (backend, scripts, notebooks); ESLint, Prettier and tsc (frontend)
	$(RUN) sh -c "ruff check . && ruff format --check . && mypy"
	$(EDA) sh -c "ruff check scripts notebooks && ruff format --check scripts notebooks"
	$(WEB) "corepack enable && pnpm install --frozen-lockfile && pnpm lint && pnpm typecheck"

format: ## Auto-fix lint issues and format code
	$(RUN) sh -c "ruff check --fix . && ruff format ."
	$(EDA) sh -c "ruff check --fix scripts notebooks && ruff format scripts notebooks"

seed: ## Insert reference assets (idempotent)
	$(COMPOSE) run --rm api marketlens seed-assets

MODE ?= incremental
ingest: ## Ingest data/raw into market_prices (MODE=full|incremental, FORCE=1 to re-process)
	$(COMPOSE) run --rm api marketlens ingest --source kaggle_btc --mode $(MODE) $(if $(FORCE),--force,)

FEATURE_SET ?= v1
features: ## Build a feature set from market_prices (FEATURE_SET=v1)
	$(COMPOSE) run --rm api marketlens build-features --feature-set $(FEATURE_SET)

feature-catalog: ## Regenerate docs/feature-catalog.md from the feature registry
	$(EDA) python scripts/gen_feature_catalog.py

openapi: ## Export the API schema to frontend/openapi.json (then `make web-gen` for the TS client)
	MSYS_NO_PATHCONV=1 $(COMPOSE) run --rm --no-deps -v "$(CURDIR):/workspace" -w /workspace api python scripts/export_openapi.py

TASK ?= classification
train: ## Walk-forward train + evaluate all models (TASK=classification|regression), log to MLflow
	$(COMPOSE) run --rm api marketlens train --feature-set $(FEATURE_SET) --task $(TASK)

report: ## Regenerate docs/experiment-report.md from the latest training runs
	$(COMPOSE) run --rm api marketlens experiment-report

FEE_BPS ?= 10
SLIPPAGE_BPS ?= 5
backtest: ## Backtest a model version after costs (MV=<id>, FEE_BPS=10, SLIPPAGE_BPS=5)
	$(COMPOSE) run --rm api marketlens backtest --model-version $(MV) --fee-bps $(FEE_BPS) --slippage-bps $(SLIPPAGE_BPS)

backtest-report: ## Regenerate docs/backtest-report.md from the latest backtests
	$(COMPOSE) run --rm api marketlens backtest-report

# ---------------------------------------------------------------- scheduled pipeline (Celery)
daily: ## Queue the daily chain now (ingest → validate → features → predict); SYNC=1 runs inline
	$(COMPOSE) run --rm api marketlens admin run-daily $(if $(SYNC),--sync,)

weekly: ## Queue train_candidates → promote_model now; SYNC=1 runs inline
	$(COMPOSE) run --rm api marketlens admin run-weekly $(if $(SYNC),--sync,)

runs: ## Show recent task runs (one row per attempt)
	$(COMPOSE) run --rm api marketlens admin runs

retry: ## Replay a failed task run with its original params (RUN=<pipeline_run id>)
	$(COMPOSE) run --rm api marketlens admin retry $(RUN) $(if $(SYNC),--sync,)

backfill: ## Features + source=backfill predictions for a range (START=YYYY-MM-DD END=YYYY-MM-DD)
	$(COMPOSE) run --rm api marketlens admin backfill --start $(START) --end $(END) $(if $(SYNC),--sync,)

# ---------------------------------------------------------------- load test (profile "loadtest")
USERS ?= 50
DURATION ?= 60s
loadtest: ## Headless Locust run vs a rate-limit-free API instance (USERS=50 DURATION=60s)
	$(COMPOSE) --profile loadtest run --rm locust --headless -u $(USERS) -r 10 -t $(DURATION) --only-summary
	$(COMPOSE) --profile loadtest stop api-load

loadtest-ui: ## Locust web UI on http://localhost:8089
	$(COMPOSE) --profile loadtest up locust

logs: ## Tail logs of all services
	$(COMPOSE) logs -f --tail=100

# ---------------------------------------------------------------- exploration (Phase 2)
data: ## Download the raw Kaggle BTC dataset into data/raw/ (needs ~/.kaggle/kaggle.json)
	$(EDA) python scripts/download_data.py

notebook: ## Start Jupyter Lab on http://localhost:8888 (Ctrl+C to stop)
	$(COMPOSE) --profile eda run --rm --no-deps --service-ports notebook

eda: ## Execute notebooks/01_eda.py end-to-end (regenerates docs/figures/ and data/interim/)
	$(EDA) sh -c "cd notebooks && jupytext --to ipynb --execute 01_eda.py"

# ------------------------------------------------------------------ website (frontend/)
web: ## Start the website dev server on http://localhost:5173 (with the API)
	$(COMPOSE) up -d --wait api frontend

web-install: ## Install frontend dependencies (pnpm, locked)
	$(WEB) "corepack enable && pnpm install --frozen-lockfile"

web-gen: ## Regenerate the typed API client from frontend/openapi.json (run `make openapi` first)
	$(WEB) "corepack enable && pnpm gen:api"

web-test: ## Run the frontend tests (Vitest + Testing Library)
	$(WEB) "corepack enable && pnpm test"

web-build: ## Production build of the website into frontend/dist
	$(WEB) "corepack enable && pnpm build"

# ------------------------------------------------------------------ backups (docs/runbooks/restore.md)
backup: ## Dump Postgres to object storage now (local: SeaweedFS; prod: scheduled daily)
	$(COMPOSE) --profile ops run --rm --build backup backup_postgres.sh

restore-drill: ## Back up, restore into a scratch DB and prove row counts match
	$(COMPOSE) --profile ops run --rm --build backup restore_drill.sh

# ------------------------------------------------------------------ releases
changelog: ## Regenerate CHANGELOG.md from Conventional Commits (TAG=vX.Y.Z for the next release)
	MSYS_NO_PATHCONV=1 docker run --rm -v "$(CURDIR):/app" -w /app orhunp/git-cliff:2.8.0 $(if $(TAG),--tag $(TAG)) -o CHANGELOG.md
