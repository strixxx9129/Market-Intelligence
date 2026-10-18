# All targets run inside containers, so the only host requirements are Docker and make.
# Windows: `winget install ezwinports.make` (or run from Git Bash / WSL).

COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
RUN     := $(COMPOSE) run --rm --no-deps api
EDA     := $(COMPOSE) --profile eda run --rm --no-deps notebook

.DEFAULT_GOAL := help
.PHONY: help up down migrate test lint format seed ingest features feature-catalog logs build data notebook eda

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

logs: ## Tail logs of all services
	$(COMPOSE) logs -f --tail=100

# ---------------------------------------------------------------- exploration (Phase 2)
data: ## Download the raw Kaggle BTC dataset into data/raw/ (needs ~/.kaggle/kaggle.json)
	$(EDA) python scripts/download_data.py

notebook: ## Start Jupyter Lab on http://localhost:8888 (Ctrl+C to stop)
	$(COMPOSE) --profile eda run --rm --no-deps --service-ports notebook

eda: ## Execute notebooks/01_eda.py end-to-end (regenerates docs/figures/ and data/interim/)
	$(EDA) sh -c "cd notebooks && jupytext --to ipynb --execute 01_eda.py"
