# Runbook: first deployment (one-time setup)

After this, every merge to `main` deploys automatically (staging, then production after
approval). Everything here is done **once per environment**; do staging first, then repeat
for production with the names in brackets. Never paste secrets into files or chat: they go
into `fly secrets` and GitHub environment secrets only.

Accounts needed: GitHub (this repo), [Fly.io](https://fly.io), [Cloudflare](https://dash.cloudflare.com)
(R2 + Pages), [Kaggle](https://www.kaggle.com) (API token for the daily download).
Tools: `flyctl`, `gh`, `psql`.

## 1. Object storage (Cloudflare R2)

1. Create buckets: `marketlens-staging-backtests`, `marketlens-staging-mlflow`,
   `marketlens-staging-backups` [`marketlens-prod-*`].
2. Create an R2 API token scoped to those buckets (Object Read & Write). Note the access
   key, secret and the S3 endpoint `https://<account-id>.r2.cloudflarestorage.com`.
3. On the backups bucket, add a lifecycle rule deleting objects older than 400 days (a
   safety net behind the script's own 30-day/12-month retention).

## 2. Fly apps, Postgres and Redis

```bash
fly apps create marketlens-api-staging            # [marketlens-api]
fly apps create marketlens-mlflow-staging         # [marketlens-mlflow]
fly ips allocate-v6 --private -a marketlens-mlflow-staging   # private only: NO public IP

fly mpg create --name marketlens-staging-db --region sin     # Managed Postgres
#   → create two databases on it: marketlens and mlflow, and note the POOLED and DIRECT URLs
fly redis create --name marketlens-staging-redis --region sin --no-replicas
#   → note the redis://default:...@fly-...upstash.io:6379 URL (eviction must be OFF: Redis is
#     also the Celery broker; evicted keys would be lost jobs)
```

## 3. Secrets (`fly secrets`, never in the repo)

```bash
fly secrets set -a marketlens-api-staging \
  DATABASE_URL="postgresql+asyncpg://<user>:<pw>@<pooled-host>/marketlens" \
  REDIS_URL="redis://default:<pw>@fly-....upstash.io:6379/0" \
  JWT_SECRET="$(openssl rand -base64 48)" \
  S3_ENDPOINT_URL="https://<account-id>.r2.cloudflarestorage.com" \
  S3_ACCESS_KEY="..." S3_SECRET_KEY="..." \
  KAGGLE_API_TOKEN="..." \
  SENTRY_DSN="..." MONITORING_WEBHOOK_URL="..."      # both optional

fly secrets set -a marketlens-mlflow-staging \
  MLFLOW_BACKEND_STORE_URI="postgresql://<user>:<pw>@<direct-host>/mlflow" \
  AWS_ACCESS_KEY_ID="..." AWS_SECRET_ACCESS_KEY="..." \
  MLFLOW_S3_ENDPOINT_URL="https://<account-id>.r2.cloudflarestorage.com"
```
Non-secret settings are in `infra/fly/*.toml` (`[env]`). The app validates everything at
startup and refuses to boot listing **all** problems (`core/config.py: deployment_problems`).
`GIT_SHA` is set by CD on every deploy.

## 4. Container images (GHCR)

The first CD run pushes `ghcr.io/<owner>/marketlens-{backend,mlflow,backup}`. Fly pulls them
by tag, so make the three packages **public** (GitHub → Packages → each → Settings →
Change visibility). They contain no secrets (secrets are injected at runtime).

## 5. Cloudflare Pages

Create two Pages projects with **Direct Upload** (CD uploads the build): `marketlens-staging`
[`marketlens`]. Their URLs are `https://marketlens-staging.pages.dev`
[`https://marketlens.pages.dev`], which must match `CORS_ORIGINS` in the fly.toml.
Create a Cloudflare API token with "Cloudflare Pages: Edit".

## 6. GitHub environments (protection rules)

```bash
gh api -X PUT repos/<owner>/<repo>/environments/staging
gh api -X PUT repos/<owner>/<repo>/environments/production \
  -F wait_timer=0 -F prevent_self_review=false \
  -f 'reviewers[][type]=User' -F 'reviewers[][id]='"$(gh api user -q .id)" \
  -F 'deployment_branch_policy[protected_branches]=true' \
  -F 'deployment_branch_policy[custom_branch_policies]=false'
```
Per environment (Settings → Environments):
- secrets: `FLY_API_TOKEN` (`fly tokens create deploy -a marketlens-api-staging`, an
  app-scoped deploy token, not your personal token), `CLOUDFLARE_API_TOKEN`,
  `CLOUDFLARE_ACCOUNT_ID`
- variables: `API_URL` (`https://marketlens-api-staging.fly.dev`), `SITE_URL`
  (`https://marketlens-staging.pages.dev`), `PAGES_PROJECT` (`marketlens-staging`)

Also protect `main` (Settings → Branches): require the CI checks and a PR.

## 7. First deploy

Set the repository variable `DEPLOY_ENABLED=true` (Settings → Secrets and variables →
Actions → Variables; until then CD only tests and builds images). Then Actions → CD → "Run
workflow" on `main` (or merge any PR). Watch for:
- **release_command** (`alembic upgrade head`) creating the schema;
- the smoke test passing (health, readiness, HTTPS redirect, HSTS/CSP, metrics not public,
  website and deep links);
- production waiting for your approval.

Then seed and load data once:
```bash
fly ssh console -a marketlens-api-staging -C "marketlens seed-assets"
fly ssh console -a marketlens-api-staging -C "marketlens admin run-daily"    # downloads + ingests
fly ssh console -a marketlens-api-staging -C "marketlens admin run-weekly"   # trains + promotes
```

## 8. Scheduled backups

Create the daily backup machine once (CD keeps its image current afterwards):
```bash
fly machine run ghcr.io/<owner>/marketlens-backup:<sha> -a marketlens-api-staging \
  --schedule daily --region sin --metadata role=backup --restart no \
  --vm-memory 512 \
  --env BACKUP_URI=s3://marketlens-staging-backups/postgres \
  --env S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com \
  --env AWS_DEFAULT_REGION=auto
fly secrets set -a marketlens-api-staging AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
```
(`DATABASE_URL` is already an app secret; use the **direct** URL for `pg_dump` if the pooler
rejects it.) Run it once by hand (`fly machine start <id>`) and check today's folder in R2,
then do a restore drill (docs/runbooks/restore.md).

## 9. Optional: custom domain

`fly certs add api.example.org -a marketlens-api` and a Pages custom domain; update
`CORS_ORIGINS`, the environment's `API_URL`/`SITE_URL`, and rebuild the site (the CSP's
`connect-src` is generated from `VITE_API_URL`).

## Costs (rough, 2026 list prices; check before creating)

Small API + 2 workers + beat + MLflow (stops when idle) on shared CPUs, the smallest Managed
Postgres plan, pay-as-you-go Upstash, R2 and Pages (free tiers): roughly $40–60/month for
production; staging sleeps when idle.
