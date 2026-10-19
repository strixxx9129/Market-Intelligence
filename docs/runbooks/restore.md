# Runbook: restore PostgreSQL from a backup

**When:** data loss or corruption (bad migration, accidental delete, broken disk), or the
monthly restore drill.
**Time to restore today's data:** ~5–15 min (dump size ~3 MB, grows with oos_predictions).
**Data loss window (RPO):** up to 24 h with daily dumps. Fly Managed Postgres also keeps
its own point-in-time recovery; use that first for "undo the last hour" and these dumps for
everything else (provider outage, account loss, restore elsewhere).

## How backups work

- A scheduled Fly machine (`metadata.role=backup`, image `marketlens-backup`) runs
  `backup_postgres.sh` daily: `pg_dump -Fc`, checks the archive is readable, writes a SHA-256
  file, uploads both to R2:
  - `s3://<bucket>/postgres/daily/YYYY-MM-DD/marketlens.dump` (kept 30 days)
  - `.../monthly/YYYY-MM/` (1st of the month, kept 12 months)
- It exits non-zero on any failure. **Check it:** `fly machines list -a marketlens-api`
  (the backup machine's last exit code) and the R2 bucket listing (today's folder exists).
- CI runs `restore_drill.sh` on every push (backup → restore → identical row counts).

## Procedure

Never restore over the live database first. Restore into a **new** database, verify, then
switch.

1. **Stop writers** so nothing writes during the switch:
   ```bash
   fly scale count worker=0 train=0 beat=0 -a marketlens-api --yes
   ```
   The API keeps serving reads from the (damaged) database until step 5.

2. **Create an empty target database** on the managed cluster (`fly mpg` dashboard, or
   `psql "$ADMIN_URL" -c 'CREATE DATABASE marketlens_restore'`).

3. **Restore** (choose `latest`, `daily/2026-10-19` or `monthly/2026-10`) from a one-off
   machine inside the private network:
   ```bash
   fly machine run ghcr.io/<owner>/marketlens-backup:<sha> -a marketlens-api --rm \
     --env TARGET_DATABASE_URL="postgresql://<user>:<pw>@<host>/marketlens_restore" \
     --env RESTORE_CONFIRM=yes --env BACKUP_URI=s3://marketlens-prod-backups/postgres \
     --env S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com \
     --env AWS_ACCESS_KEY_ID=... --env AWS_SECRET_ACCESS_KEY=... \
     -- restore_postgres.sh latest
   ```
   The script verifies the SHA-256 before touching the database and stops on the first
   error (`--exit-on-error`). It prints the Alembic revision of the restored data.

4. **Verify** before switching:
   ```sql
   SELECT version_num FROM alembic_version;            -- matches the deployed code's head?
   SELECT max(ts) FROM market_prices;                    -- how recent is the data?
   SELECT count(*) FROM model_versions WHERE stage = 'production';  -- exactly 1
   ```
   If the revision is older than the code, the next deploy's release step migrates it
   forward; or run `fly ssh console -a marketlens-api -C "alembic upgrade head"` with the
   new `DATABASE_URL`.

5. **Switch** the app to the restored database (the secret change rolls all machines):
   ```bash
   fly secrets set DATABASE_URL="postgresql+asyncpg://...@.../marketlens_restore" -a marketlens-api
   ```

6. **Resume and catch up:** the scheduled jobs are idempotent (UPSERTs on natural keys), so
   re-running them refills the gap since the backup:
   ```bash
   fly scale count worker=1 train=1 beat=1 -a marketlens-api --yes
   fly ssh console -a marketlens-api -C "marketlens admin run-daily"
   fly ssh console -a marketlens-api -C "marketlens admin flush-cache"
   ```
   Then check `/api/v1/status` (freshness, last runs). Forecasts for days lost in the gap can be
   recreated with `marketlens admin backfill --start ... --end ...` (they're labelled
   `source=backfill`, never presented as live).

7. Keep the damaged database for 7 days (forensics), then drop it. Write a short incident
   note: cause, RPO actually lost, what to change.

## Monthly drill (production backups)

Restore last night's dump into a scratch database and compare, without touching production:
```bash
fly machine run ghcr.io/<owner>/marketlens-backup:<sha> -a marketlens-api --rm \
  --env DATABASE_URL=<prod url, read-only role is enough> \
  --env DRILL_DATABASE_URL=<scratch db url> --env DRILL_ADMIN_URL=<any db on the cluster> \
  --env BACKUP_URI=s3://marketlens-prod-backups/drill ... -- restore_drill.sh
```
Locally: `make restore-drill` (dev database → SeaweedFS → `marketlens_drill`).
