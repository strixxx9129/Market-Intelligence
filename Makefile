# All targets run inside containers, so the only host requirements are Docker and make.
# Windows: `winget install ezwinports.make` (or run from Git Bash / WSL).

# Commit being run, recorded with every training run (reproducibility). "-dirty" = uncommitted changes.
export GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo unknown)$(shell git diff --quiet HEAD 2>/dev/null || echo -dirty)

COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
RUN     := $(COMPOSE) run --rm --no-deps api
EDA     := $(COMPOSE) --profile eda run --rm --no-deps notebook

.DEFAULT_GOAL := help
.PHONY: help up down migrate test lint format seed ingest features feature-catalog train report backtest backtest-report logs build data notebook eda

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

test: ## Run the test suite (needs `make up` for postgres/redis)
	$(COMPOSE) run --rm api pytest

lint: ## ruff (lint + format check) and mypy --strict; ruff on scripts/ and notebooks/
	$(RUN) sh -c "ruff check . && ruff format --check . && mypy"
	$(EDA) sh -c "ruff check scripts notebooks && ruff format --check scripts notebooks"

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

logs: ## Tail logs of all services
	$(COMPOSE) logs -f --tail=100

# ---------------------------------------------------------------- exploration (Phase 2)
data: ## Download the raw Kaggle BTC dataset into data/raw/ (needs ~/.kaggle/kaggle.json)
	$(EDA) python scripts/download_data.py

notebook: ## Start Jupyter Lab on http://localhost:8888 (Ctrl+C to stop)
	$(COMPOSE) --profile eda run --rm --no-deps --service-ports notebook

eda: ## Execute notebooks/01_eda.py end-to-end (regenerates docs/figures/ and data/interim/)
	$(EDA) sh -c "cd notebooks && jupytext --to ipynb --execute 01_eda.py"
