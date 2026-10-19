# ADR 0012: Deployment: Fly.io + Cloudflare, SHA-tagged images, migrations as a release step, fail-fast config, tested backups

- Status: Accepted
- Date: 2026-10-19

## Context

MarketLens needs a public URL with automated, safe deploys. The constraints come from
CLAUDE.md:
- the API only reads (rule 5);
- jobs must be idempotent (rule 2);
- reproducibility needs the deployed SHA (rule 3);
- data must carry its licence (rule 7).

It's a portfolio-scale project, so cost and operational simplicity matter as much as
robustness.

## Decision

### Hosting
- **Fly.io runs the backend.** One app per environment runs one image with four process groups:
  - `app`: Gunicorn with Uvicorn workers;
  - `worker`: the daily queues and monitoring;
  - `train`: weekly training, one task per process;
  - `beat`: the scheduler.
- **Fly Managed Postgres** with the pooled URL (`DB_PGBOUNCER=true` disables asyncpg prepared
  statements, which break under transaction pooling).
- **Upstash Redis**, with eviction off, because it is also the Celery broker.
- **A private MLflow app**, reachable only over flycast, with no public IP. MLflow has no auth,
  so being unreachable from the internet is the access control.
- **Cloudflare R2** (S3, no egress fees) holds artifacts, backtests and backups.
- **Cloudflare Pages** hosts the static website.
- Staging and production are separate apps, databases, buckets and Pages projects, with
  identical shape.

### CD
- `cd.yml` runs on every merge to main:
  1. CI as a `workflow_call` (the deployed code is the tested code);
  2. three images pushed to GHCR, **tagged with the commit SHA** (never `latest`), with
     provenance and an SBOM;
  3. the reusable `deploy.yml` for staging;
  4. then for production, behind GitHub environment protection: a required reviewer and
     `main` only.
- Each deploy:
  - deploys MLflow, then the API app;
  - re-asserts `beat=1`;
  - updates the backup machine's image;
  - builds the site with the environment's API URL and deploys it to Pages;
  - runs `scripts/smoke_test.sh`, which checks health, readiness, version = SHA, HTTPS redirect,
    HSTS/CSP, that metrics are not public, and SPA deep links.
- The `cd-main` concurrency group makes deploys queue instead of racing.

### Migrations as a release step
`release_command = "alembic upgrade head"` runs **once**, in a temporary machine with the new
image, **before** any running process is replaced.

What breaks without it:
- **Migrating on app startup:** N replicas race to run the same DDL at boot, and some fail
  half-way.
- **Migrating after deploy:** new code runs against the old schema and crashes on missing
  columns.
- **A failed migration inside a rolling deploy:** leaves a mix of old and new containers.

With a release step, a failed migration aborts the deploy and the old release keeps serving.
Because old code runs against the new schema for a few seconds during the rollout (and
indefinitely after a code rollback), migrations must be backward-compatible:
expand → migrate data → contract in a later release.

### Exactly one beat
Beat is a clock, not a worker: each instance enqueues every scheduled job on its own, so two
beats would run the 00:15 chain twice. Our idempotency (UPSERTs) and Redis locks would keep
the data correct, but the duplicates would still:
- double the work;
- double alerts and Kaggle calls;
- muddy the run history.

How a single instance is guaranteed:
- beat is its own process group, never autoscaled;
- CD runs `fly scale count beat=1` after every deploy;
- Fly updates machines in place, so a deploy never runs the old and new beat at once;
- the prod compose sets `replicas: 1`.

The schedule file is disposable: crontab entries are recomputed from the clock.

### Fail-fast configuration
In `staging`/`prod`, `Settings` validation collects every unsafe or missing value and refuses
to start, in every process (API, workers, beat, release step). The checks:
- the JWT secret is not the dev default and has at least 32 characters;
- the DB/Redis are not localhost or the dev password;
- CORS is not empty, `*` or `http://`;
- S3 and MLflow are set;
- `GIT_SHA` is set;
- the log level is not `DEBUG`.

Workers additionally require Kaggle credentials when `KAGGLE_REFRESH=true`. A misconfigured
release fails its release step or health check and never takes traffic; one deploy reports
all the problems at once.

### Security
- **HTTPS only:** `force_https` at Fly's edge (HTTP gets a 301), plus HSTS for a year, so
  browsers refuse plain HTTP even on the first typed URL after a visit. Pages is HTTPS-only.
- **CORS:** an exact `https://` origin list, `allow_credentials=False` (auth is a header, never
  a cookie), and explicit methods and headers. A foreign site's JavaScript can't read API
  responses with a visitor's token.
- **Security headers (API):**
  - `nosniff`;
  - `X-Frame-Options: DENY` and `frame-ancestors 'none'`, so the API can't be framed
    (clickjacking);
  - CSP `default-src 'none'`, so an API response rendered as a page can't run anything;
  - a narrower CSP only for `/docs`;
  - `Referrer-Policy: no-referrer` and `Permissions-Policy`.
- **Website CSP:** `script-src 'self'` (the theme script moved out of the HTML for this),
  `connect-src` limited to the environment's API (generated at build time), `object-src
  'none'`, and `frame-ancestors 'none'`. An injected `<script>` or an exfiltration request to
  another host is blocked by the browser.
- **Real client IP:** `TRUSTED_CLIENT_IP_HEADER=Fly-Client-IP`. Fly's edge sets it and
  overwrites any value the client sent. `X-Forwarded-For` is never trusted, because clients can
  forge it to dodge per-IP rate limits. Without this, every visitor would share the proxy's IP
  and one global anonymous rate limit.
- **Private metrics:** `/metrics` is removed from the public app when deployed. Gunicorn serves
  the multiprocess aggregate on a private port (9091) that Fly scrapes over the internal
  network.
- **Images:**
  - multi-stage, with a non-editable venv copied into a slim runtime;
  - no uv, pip, compilers or curl, so an attacker who gets in finds fewer tools;
  - uid 1000, with tini as PID 1 for graceful SIGTERM (in-flight requests and tasks finish)
    and zombie reaping;
  - a `HEALTHCHECK`.
- **Prod compose:** a read-only root filesystem, `cap_drop: ALL` and `no-new-privileges`.
- **Supply chain:**
  - SHA-tagged images with provenance and an SBOM;
  - Dependabot for uv, npm, Actions and Docker, grouped weekly;
  - CodeQL (`security-extended`) for Python, TypeScript and the workflows themselves;
  - actionlint and shellcheck in CI;
  - least-privilege `permissions` per job;
  - app-scoped Fly deploy tokens kept in GitHub environment secrets.
- **Data licence (rule 7):** the CC BY-SA 4.0 attribution appears in price/asset API responses,
  the website footer and the README.

### Reliability
- **Readiness vs liveness:** Fly routes only to machines whose `/readyz` (Postgres and Redis)
  passes, and liveness never depends on the database.
- **Gunicorn:**
  - `WEB_CONCURRENCY` workers, capped to respect the DB connection limit;
  - `max_requests` with jitter, which recycles workers to bound memory creep;
  - a 30 s worker timeout;
  - a 20 s graceful drain on deploy.
- **The worker and training processes are separate**, so a training job can't starve the
  00:15 forecast. Training recycles its process after each task to return memory.

### Backups
- `scripts/backup_postgres.sh` runs daily on a Fly scheduled machine inside the private network,
  so the DB is never exposed to the internet:
  1. `pg_dump -Fc --no-owner`;
  2. check the archive with `pg_restore --list`;
  3. write a SHA-256 file;
  4. upload to R2 (daily for 30 days, monthly for 12);
  5. prune with a guard so that a name which isn't a date is never deleted.

  It exits non-zero on any failure.
- `restore_postgres.sh`:
  - verifies the checksum before touching the database;
  - restores with `--exit-on-error`;
  - requires `RESTORE_CONFIRM=yes`.
- `restore_drill.sh` backs up, restores into a scratch DB and compares exact row counts and the
  Alembic revision. It runs **in CI on every push** and monthly against production backups.
  These dumps sit on top of the provider's own point-in-time recovery: they protect against
  account loss or a provider outage and allow restoring anywhere.

## Consequences

- Fly-specific pieces: `fly.toml`, the release command, flycast, the client-IP header and
  scheduled machines. The prod compose plus Caddy is the escape hatch: the same images and
  process layout on any VM.
- GHCR packages must be public for Fly to pull them. They contain no secrets.
- `superfly/flyctl-actions` is used from its default branch: Fly publishes no release tags.
- Rough cost: about $40–60/month for production. Staging and MLflow stop when idle.
- The raw Kaggle download isn't on a volume: a restarted worker re-downloads it (about a
  minute).
- Remaining hardening, tracked in PROGRESS:
  - move the refresh token to an httpOnly cookie;
  - pin Actions by SHA (Dependabot can then keep them updated);
  - add Prometheus alert rules for SLOs.

## Alternatives considered

- **Render:** similar (pre-deploy command, workers, cron), but no native object storage and less
  control over the private network. A good fallback.
- **Kubernetes:** Jobs for migrations and CronJobs for backups are first-class, but the cluster
  is overkill for one API and three workers.
- **Migrations in the app entrypoint:** races between replicas, and slow or failed boots (see
  above).
- **Only the provider's backups:** a single point of failure, tied to the account, and never
  restore-tested by us.
- **`latest` tags:** you can't tell what's running, and you can't roll back precisely.
