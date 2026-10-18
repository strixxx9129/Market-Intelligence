# ADR 0001: Monorepo with a modular monolith backend

- Status: Accepted
- Date: 2026-10-18

## Context

MarketLens has a Python backend (API, ingestion, features, ML, backtesting, monitoring,
scheduled jobs), a React frontend, and infrastructure config. It is maintained by one person
initially and must be easy for outside contributors to clone, run and extend through plugin
registries (connectors, features, models).

## Decision

1. **One repository** containing `backend/`, `frontend/`, `infra/`, `docs/`.
2. The backend is a **modular monolith**: a single Python package `marketlens` with internal
   modules (`ingestion`, `features`, `ml`, `backtest`, `monitoring`, ...) and strict layering
   *routers → services → repositories*. The API process and the Celery worker/beat run the
   **same codebase** in different processes (one Docker image, different commands).
3. **src layout** (`backend/src/marketlens`) so tests import the installed package, not the
   working directory.

## Consequences

- One clone, one `make up`, one CI pipeline. Cross-cutting changes (schema + API + UI) land in a
  single PR and are reviewed together.
- No network hops or API versioning between internal modules; refactoring is cheap.
- Module boundaries are enforced by convention and review, not by process isolation. If a module
  later needs independent scaling (e.g. training), it can be extracted because it already talks
  through services.
- CI builds everything on every change; path filters can be added when that becomes slow.

## Alternatives considered

- **Microservices** (separate ingestion/ML/API services): operational overhead (deploys,
  contracts, tracing) far exceeds the benefit at this size.
- **Polyrepo** (separate frontend/backend repos): harder for contributors and for atomic changes.
