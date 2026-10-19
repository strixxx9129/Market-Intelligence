# Runbook: flush the API cache

**When:** the website shows data that is already fixed in PostgreSQL (manual DB correction,
restore, a writer that forgot to invalidate), or after changing an endpoint's response shape
without a version bump.

## How the cache works (ADR 0009)

Cache-aside in Redis per namespace: `assets`, `prices`, `features`, `forecast`, `models`,
`backtests`, `status`. Keys embed a namespace version; "flushing" increments the version, so
every old key becomes unreachable at once and expires via its TTL (≤ 1 h). No `KEYS`/`SCAN`,
no `FLUSHALL`: Redis is also the Celery broker, and FLUSHALL would delete queued jobs and
task locks.

Writers already invalidate automatically: new prediction → `forecast`/`status`; promotion →
`models`/`forecast`/`status`; ingest/features → `assets`/`prices`/`features`.

## Flush

```bash
fly ssh console -a marketlens-api -C "marketlens admin flush-cache"                       # everything
fly ssh console -a marketlens-api -C "marketlens admin flush-cache --namespace forecast"  # one
```
Local: `docker compose -f infra/docker-compose.yml --env-file .env run --rm api marketlens admin flush-cache`.

## Verify

```bash
curl -sI https://<api>/api/v1/assets | grep -i x-cache   # first call: MISS, second: HIT
```

## Browsers and the CDN

- API responses carry `ETag` + short `Cache-Control: max-age`; browsers revalidate quickly.
- The website's `index.html` is `no-cache` and assets are content-hashed: a new deploy is
  picked up on the next page load. A stuck user can hard-reload (Ctrl+Shift+R).
- **Never** use `redis-cli FLUSHALL` / `FLUSHDB` on production (see above).
