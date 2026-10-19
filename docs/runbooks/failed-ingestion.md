# Runbook: failed or stale ingestion

**Symptoms:** monitoring alert `freshness_hours` (newest candle closed > 36 h ago), `/status`
shows `stale_data` or a `job_failed` alert for `ingest`/`validate`, the website's forecast
date stops moving.

The daily chain is `ingest → validate → build_features → predict` (Celery, 00:15 UTC).
Transient errors (network, Kaggle 5xx) retry automatically with backoff (up to 5 times);
permanent errors (bad file, validation failure) stop the chain on purpose. A blocked chain
is safer than forecasts built on bad data.

## 1. Find the failing step

```bash
fly ssh console -a marketlens-api -C "marketlens admin runs --limit 20"   # one row per attempt
```
Local: `make runs`. Look at the first failed step and its `error`.

| error | meaning | action |
|---|---|---|
| `TransientError` … then success | retried and recovered | nothing |
| `TransientError` × 6 (`failed`) | Kaggle/network down for ~1 h | step 3 later |
| Kaggle 401/403 | token expired/revoked | rotate `KAGGLE_API_TOKEN` (step 2) |
| `PermanentError: validation` | data failed checks; rows quarantined in `data_quality_issues` | step 4 |
| `locked` / skipped | another run holds the lock | wait; locks expire on their own |
| worker never ran | worker or beat machine down | `fly status -a marketlens-api`; restart |

## 2. Credentials

```bash
fly secrets set KAGGLE_API_TOKEN=... -a marketlens-api   # restarts machines
```
Workers refuse to start without Kaggle credentials when `KAGGLE_REFRESH=true`, so a missing
secret shows up as a crashing worker, not as a silent 00:15 failure.

## 3. Re-run

Everything is idempotent (UPSERT on `(asset_id, interval, ts)`, file hashes for raw files),
so re-running is always safe:
```bash
fly ssh console -a marketlens-api -C "marketlens admin retry <pipeline_run id>"  # same params
fly ssh console -a marketlens-api -C "marketlens admin run-daily"               # whole chain
```

## 4. Validation failures

```sql
SELECT check_name, severity, ts, details FROM data_quality_issues ORDER BY last_seen_at DESC LIMIT 20;
```
Common causes: the source changed format, a gap in the raw data, a price spike. If the data
is genuinely wrong upstream, leave the chain blocked and record it; **don't** loosen the
checks to push bad candles through. If a check is wrong, fix it in code with a test, deploy,
re-run.

## 5. After recovery

- `marketlens admin flush-cache --namespace status` (the next status read is fresh anyway
  within 30 s).
- Missing forecast days: `marketlens admin backfill --start YYYY-MM-DD --end YYYY-MM-DD`
  (stored as `source=backfill`; the live track record only counts live forecasts).
- The freshness alert clears itself on the next hourly check.
