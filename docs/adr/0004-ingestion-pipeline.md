# ADR 0004: Ingestion pipeline: ETL with quarantine, layered idempotency, connector plugins

- Status: Accepted
- Date: 2026-10-18

## Context

Raw market data (first: the Kaggle Bitstamp 1-minute CSV, 7.8M rows) must become validated
daily candles in `market_prices`. Jobs will be re-run by hand, by schedules and by retries, so
every run must be safe to repeat. Data problems must be visible, never silently lost.
Contributors should be able to add new data sources without touching the pipeline.

## Decision

1. **ETL, not ELT.** Transform (minute → daily aggregation, validation) happens in Python
   *before* loading; only daily candles reach PostgreSQL. The raw file is kept in `data/raw/`
   with its SHA-256 recorded in `raw_ingestions`, so it acts as our replayable source of truth.
   We load 5,404 daily rows instead of 7.8M minute rows we don't query.
2. **Connector plugin interface.** `BaseConnector.fetch(asset, start, end) -> FetchResult`
   (canonical candles + issues) and optional `fingerprint()`. Registered via a decorator
   (built-ins) or the `marketlens.connectors` entry-point group (third-party packages).
3. **Quarantine instead of delete.** Rows that fail checks are excluded from the load but stored
   in `data_quality_issues` (check, severity, raw values). `error` = not loaded; `warning` =
   loaded and flagged. A row-count invariant asserts nothing disappears silently.
4. **Layered idempotency.** (a) skip inputs whose hash is already in `raw_ingestions`;
   (b) UPSERT candles on `UNIQUE(asset_id, interval, ts)` with `WHERE … IS DISTINCT FROM`, so
   unchanged rows are not rewritten; (c) UPSERT issues on their natural key (with
   `NULLS NOT DISTINCT`); (d) candles, issues, ledger row and final run status commit in ONE
   transaction, while the `pipeline_runs` row is committed first so failures stay visible.
5. **Streaming aggregation.** The connector reads 1M-row chunks as strings and reduces each to
   per-day partial aggregates that are merged at the end: about one chunk of memory, and
   identical results for any chunk size (tested with 333 rows).
6. **Incomplete periods are withheld.** The still-forming last day is not loaded (no
   look-ahead) and is reported as `incomplete_period`; incremental mode loads it once complete.
7. **The pipeline never runs in the API process** (rule 5). It runs from the CLI
   (`marketlens ingest`, `make ingest`) and later from Celery, all calling the same service.

## Consequences

- Re-running any ingestion is cheap and safe: same file → skipped in seconds; `--force` → 0 rows
  changed.
- Minute-level data is not in the database. If a later phase needs it (e.g. intraday features),
  the raw file can be replayed with a new connector or interval, or loaded into a staging table
  (moving that part towards ELT).
- Validation rules live in code (Pandera + explicit warnings); changing them requires a
  `--force` re-run to re-evaluate history.
- `data_quality_issues` grows with problems found, not with re-runs.

## Alternatives considered

- **ELT** (load all minute rows into a staging table, aggregate in SQL): more flexible for ad-hoc
  SQL, but 7.8M rows of storage and a slower load for data we don't query today.
- **Deleting bad rows**: simpler, but loses the evidence needed to debug sources and to
  notice systematic problems (e.g. an exchange outage).
- **Truncate-and-reload**: idempotent but destructive; breaks incremental loads and anything
  reading the table during a reload.
