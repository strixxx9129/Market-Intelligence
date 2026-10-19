# MarketLens

Open-source **Market Intelligence & ML Monitoring Platform**. It ingests historical Bitcoin (later
stocks) data, builds leakage-free features, trains and compares ML models with walk-forward
validation, backtests strategies after costs, monitors data/model health, and serves everything
through a public FastAPI API and a React website.

> **Educational software, not financial advice.** Nothing MarketLens produces is a
> recommendation to buy or sell anything.

## Quick start

Requirements: **Docker** (with Compose v2) and **make**.
On Windows: `winget install ezwinports.make`, or run the commands from Git Bash / WSL.

```bash
cp .env.example .env
make up        # build + start the stack, waits until every service is healthy
make migrate   # create the database schema
make test      # run the test suite inside the api container
```

Then open:

| Service | URL |
|---|---|
| API docs (Swagger) | http://localhost:8000/docs |
| Liveness / readiness | http://localhost:8000/healthz · http://localhost:8000/readyz |
| MLflow | http://localhost:5000 |
| S3 API (SeaweedFS) | http://localhost:8333 |
| Grafana (user `admin`, password `GRAFANA_ADMIN_PASSWORD`) | http://localhost:3001 |
| Prometheus | http://localhost:9090 |

Other targets: `make seed` (reference assets), `make ingest` (raw data → `market_prices`;
`MODE=full|incremental`, `FORCE=1`), `make features` (build feature set `v1`), `make feature-catalog`,
`make train TASK=classification|regression`, `make report`, `make lint`, `make format`, `make logs`,
`make down`, `make help`. Features: [docs/feature-catalog.md](docs/feature-catalog.md).
Models: [docs/experiment-report.md](docs/experiment-report.md) (walk-forward, vs baselines).
Backtests: `make backtest MV=<model version id>`, `make backtest-report` → [docs/backtest-report.md](docs/backtest-report.md)
(hypothetical, after costs, vs buy-and-hold; not financial advice).

Scheduled pipeline (Phase 7): `make up` starts `worker`, `worker-train` and `beat`. Beat runs the daily
chain at 00:15 UTC (Kaggle download → ingest → validate → features → predict), scores forecasts at
01:00 and retrains on Sundays 02:00 behind a promotion gate. `make daily` / `make weekly` run them now,
`make runs` shows attempts, `make retry RUN=<id>` replays a failed run, `make backfill START=… END=…`.

Public API (Phase 8): interactive docs at http://localhost:8000/docs. Anonymous reads are limited
to 60/min per IP; register (`POST /api/v1/auth/register`), log in, then create a key under
`/api/v1/me/api-keys` and send it as `X-API-Key` (1000/min). First admin:
`docker compose -f infra/docker-compose.yml --env-file .env run --rm api marketlens admin create-user --email you@example.org --role admin`.
Load test: `make loadtest` (Locust, headless) or `make loadtest-ui` (http://localhost:8089).

Monitoring (Phase 9): Beat checks freshness hourly, input/prediction drift (PSI + KS vs the
model's training data) at 00:45 and live log loss/Brier/accuracy at 01:15. Results go to
`monitoring_metrics`. Breaches go to `monitoring_alerts` and to `MONITORING_WEBHOOK_URL`
(Discord or Slack), at most once per 24 h per alert. Grafana's "MarketLens overview" dashboard
shows API latency, Celery tasks and data/model health. Set `SENTRY_DSN` to enable Sentry.
See [ADR 0010](docs/adr/0010-monitoring.md) for how to read PSI.

Data exploration (Phase 2): put a Kaggle token in `~/.kaggle/` (`kaggle.json` or `access_token`),
then `make data` (download + SHA-256 manifest), `make notebook` (Jupyter Lab on
http://localhost:8888) or `make eda` (run `notebooks/01_eda.py` end-to-end). Findings:
[docs/eda-report.md](docs/eda-report.md).

## Architecture (Phase 1)

```
            ┌────────────┐     ┌────────────┐
 browser ──►│  api       │────►│ postgres16 │◄── mlflow (tracking metadata)
            │  FastAPI   │────►│ redis 7    │◄── worker / beat (Celery)
            └────────────┘     └────────────┘
                                seaweedfs (S3) ◄── mlflow (model artifacts)
```

A modular monolith in one monorepo; see [ADR 0001](docs/adr/0001-monorepo-modular-monolith.md).
Backend layering: routers (HTTP only) → services (logic) → repositories (SQL only).

## Repository layout

```
backend/   Python package `marketlens` (src layout), tests, Alembic migrations
frontend/  React + TypeScript (later phase)
infra/     docker-compose and service config
docs/      PROGRESS.md and ADRs
```

## Local development without Docker (optional)

Install [uv](https://docs.astral.sh/uv/), then in `backend/`: `uv sync`, and point
`DATABASE_URL` / `REDIS_URL` at `localhost`. Enable git hooks with
`uv tool install pre-commit && pre-commit install`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Licensed under [Apache-2.0](LICENSE).
