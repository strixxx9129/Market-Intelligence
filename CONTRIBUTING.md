# Contributing to MarketLens

Thanks for helping! This is a stub; plugin guides for connectors, features and models arrive in
later phases.

## Workflow

1. Fork, create a branch (`feat/...`, `fix/...`).
2. `cp .env.example .env && make up && make migrate`.
3. Make your change **with tests**.
4. `make lint && make test` must pass (CI runs the same checks on every PR).
5. Open a PR with a [Conventional Commit](https://www.conventionalcommits.org/) title
   (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).

## Ground rules

- **No look-ahead bias.** A feature at time *t* may only use data available at or before *t*.
  Splits are chronological; never random.
- **Idempotent jobs.** Every job must be safe to re-run (UPSERT on natural keys).
- **UTC everywhere** (`timestamptz`, timezone-aware `datetime`).
- **The API never trains models or calls external data sources** during a request.
- **Type hints everywhere**; mypy runs in strict mode on `backend/src`.
- **Schema changes go through Alembic**: `docker compose ... run --rm api alembic revision --autogenerate -m "..."`, then review the generated file.
- Only add data sources whose licence allows redistribution.
- Never commit secrets; configuration comes from environment variables.

## Optional: git hooks

```bash
uv tool install pre-commit
pre-commit install
```
