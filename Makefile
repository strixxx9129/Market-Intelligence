# All targets run inside containers, so the only host requirements are Docker and make.
# Windows: `winget install ezwinports.make` (or run from Git Bash / WSL).

COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
RUN     := $(COMPOSE) run --rm --no-deps api

.DEFAULT_GOAL := help
.PHONY: help up down migrate test lint format seed logs build

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

lint: ## ruff (lint + format check) and mypy --strict
	$(RUN) sh -c "ruff check . && ruff format --check . && mypy"

format: ## Auto-fix lint issues and format code
	$(RUN) sh -c "ruff check --fix . && ruff format ."

seed: ## Insert reference data (idempotent)
	$(COMPOSE) run --rm api python -m marketlens.db.seed

logs: ## Tail logs of all services
	$(COMPOSE) logs -f --tail=100
