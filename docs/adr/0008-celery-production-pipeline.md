# ADR 0008: Production pipeline on Celery: chains, per-attempt runs, locks, gated promotion

- Status: Accepted
- Date: 2026-10-19

## Context

The daily cycle (download → ingest → validate → features → predict), outcome scoring and weekly
retraining must run unattended, survive worker crashes and flaky networks, never duplicate
data, and never put an unproven model in front of users.

## Decision

1. **Celery with Redis**, JSON-only serialisation, `acks_late` + `task_reject_on_worker_lost`
   (a crashed task is re-delivered), prefetch 1, UTC. Four queues (`ingest`, `features`,
   `predict`, `train`) consumed by two worker services: `worker` (daily lanes, concurrency 2)
   and `worker-train` (train only, concurrency 1, fresh process per task).
2. **Thin tasks** call existing services through `run_reliably`, which for every ATTEMPT:
   writes a `pipeline_runs` row (`task:<job>`, params, attempt, Celery task/root ids, status,
   error); takes a Redis lock (`SET NX EX` + token, Lua compare-and-delete release; a held lock
   → `skipped` + chain stops); classifies errors: transient → `retrying` and Celery autoretry
   (exponential backoff, jitter, max 600 s, max 5 retries), anything else → `failed` at once.
   Service-level runs link to their task attempt via `parent_run_id`.
3. **Chains with immutable signatures**: daily `ingest → validate → build_features → predict`
   (Beat 00:15 UTC), `evaluate_outcomes` (01:00), weekly `train_candidates → promote_model`
   (Sun 02:00). `validate` raises a permanent error on gaps / quarantined candles, which stops
   the chain before features or forecasts are built on bad data.
4. **Idempotent writes everywhere**: predictions UPSERT on (model_version, asset, as_of,
   horizon); outcomes on prediction_id; ingest/features already idempotent; training skipped
   when dataset hash and git SHA match the last successful run. Backfills are stored with
   `source='backfill'` and never overwrite live forecasts (they are not out-of-sample).
5. **Promotion gate**: the staging candidate (best non-baseline of the latest run) is promoted
   only if its mean fold log-loss improvement over the run's best baseline is ≥
   `PROMOTION_MIN_LOGLOSS_IMPROVEMENT` (0.001) and it is worse in ≤ `PROMOTION_MAX_WORSE_FOLDS`
   (8) folds. Every decision (`promoted` / `rejected` / `fallback`) and its reasons go to
   `promotion_decisions`. With nothing in production, the best baseline serves as an explicit,
   labelled fallback.
6. **The worker downloads data** (Kaggle refresh in the connector, credentials mounted
   read-only into workers only); the API never calls external sources (rule 5).
7. Admin: `marketlens admin retry <run>` replays a failed task run with its recorded params;
   `backfill`, `run-daily`, `run-weekly`, `runs`.

## Consequences

- Duplicate deliveries, retries and manual replays are harmless by construction; locks only
  save wasted work.
- Lock TTLs must exceed job durations (ingest 30 min, training 4 h); a job that outlives its TTL
  can overlap a newer run (writes are still idempotent).
- Single Redis instance = single point of failure for locks/broker (acceptable here).
- Today no candidate passes the gate (Phase 5), so production serves the baseline. That's the
  honest state, visible in `promotion_decisions` and in every forecast's model_version.
- Tests run tasks eagerly with a redelivery helper plus one real in-process worker test; Celery's
  result store is not used as a source of truth (pipeline_runs is).
