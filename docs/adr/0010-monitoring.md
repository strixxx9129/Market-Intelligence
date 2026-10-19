# ADR 0010: Monitoring: stored metrics, significance-gated PSI drift, deduplicated alerts, Prometheus + Grafana

- Status: Accepted
- Date: 2026-10-19

## Context

A forecasting service can fail quietly: the feed stops, an input changes units, or markets move
into a regime the model never saw. The API keeps serving numbers in every one of these cases. We
need to notice before users do, without paging someone for every wobble, and in a way that a
human can audit afterwards.

## Decision

1. **Every check writes rows to `monitoring_metrics`**, whether or not it breaches:
   (metric_name, asset_id, model_version_id, ts, value, threshold, breached, details JSONB),
   UNIQUE with NULLS NOT DISTINCT, so re-running a check UPSERTs (rule 2). `details` keeps the
   evidence: PSI bins, shares, windows, n, the reason a value is missing. History is queryable
   in SQL and shown in Grafana.
2. **Freshness** = hours since the newest daily candle *closed* (ts + 24 h), per active asset.
   Alerts above `FRESHNESS_MAX_HOURS` (36 h: one missed daily run plus slack). Runs hourly.
3. **Feature drift**: the last `DRIFT_WINDOW_DAYS` (30) feature rows vs the rows inside the
   production model's own training window. Bins are reference quantiles, fixed per model
   version, with open-ended outer bins. The alert rule is **large AND significant**:
   - large: PSI > `PSI_ALERT` (0.25);
   - significant: PSI above its chi-squared critical value
     (PSI·n_ref·n_cur/(n_ref+n_cur) ≈ χ²(bins−1) when nothing changed), **or** KS p < `KS_ALPHA`.
   The bin count adapts to the sample size (about 6 recent rows per bin, so 5 bins for 30 days).
   Categorical inputs (`day_of_week`) use category shares. KS is also stored as its own metric
   (`ks:<feature>`), as a second opinion; it never alerts on its own.
4. **Prediction drift**: PSI of live P(up) vs the model's walk-forward (out-of-sample) P(up),
   under the same rule. Marked not applicable when the reference has no spread (baselines).
5. **Live performance**: rolling 30/90-day log loss, Brier and directional accuracy from
   `prediction_outcomes`. Log loss alerts when it is worse than
   min(walk-forward log loss, ln 2) + `PERFORMANCE_LOGLOSS_MARGIN`. Accuracy is recorded next to
   always-up over the same window. Below `PERFORMANCE_MIN_N` (20) labelled forecasts the value
   is NULL with a reason: no verdict from a handful of days.
6. **Alerts**: each breach becomes a `monitoring_alerts` row (delivered / suppressed / failed /
   logged). The dedup key is metric + asset + model, with a `MONITORING_ALERT_COOLDOWN_HOURS`
   (24 h) window. A failed delivery doesn't count as notified, so the next run retries it. The
   webhook payload `{"content", "text"}` works for both Discord and Slack. With no webhook set,
   alerts are logged. `/status` lists the last 24 h of alerts.
7. **Schedule** (Beat, queue `monitoring` on the daily worker): freshness hh:05, drift 00:45
   (after the daily chain), performance 01:15 (after outcomes are scored).
8. **Prometheus + Grafana** in compose, published on 127.0.0.1 only:
   - The API exposes `/metrics`: request count and latency labelled by full route template.
     FastAPI 0.146 keeps included routers nested, so the template is rebuilt from the include
     prefix.
   - Celery workers use prometheus-client multiprocess mode: prefork children write sample
     files and the main process serves the aggregate on :9808.
   - Grafana provisions both datasources (Prometheus, PostgreSQL) and one dashboard from
     `infra/grafana/`.
9. **Sentry** is optional: initialised only when `SENTRY_DSN` is set, with `send_default_pii=False`.
10. **Drift triggers investigation, never automatic retraining.**

## Why significance-gated PSI (and not the textbook rule alone)

The usual PSI bands (0.10 / 0.25) come from credit scoring, where samples run to thousands of
rows. With 30 daily rows and 10 bins, PSI from sampling noise alone averages
(bins−1)(1/n_ref + 1/n_cur) ≈ 0.3, which already crosses the alert line. Our simulation test
(`test_drift_rule_rarely_cries_wolf_but_catches_a_real_shift`) measured the difference:

- the textbook rule alerted on more than 30% of no-change runs;
- the gated rule alerted on at most 2% of no-change runs;
- the gated rule still caught more than 80% of tripled-volatility or shifted-mean windows.

The chi-squared reading of PSI catches changes in spread, which KS misses on small samples. KS
catches shifts of the middle. Alerting fatigue is a real failure mode: people mute channels that
cry wolf.

## Why drift should not retrain automatically

A drift alert says *the inputs look different*, not *the model is wrong*, and not *retraining
will help*. The cause can be:
- a broken input (unit change, stuck feed): retraining would teach the model the bug;
- a genuine regime change: worth knowing, but there may be too little new data to learn from;
- harmless: the model may not even use that input. The current production baseline ignores
  features entirely.

So a person looks at which bins moved, checks the data, and decides. Labelled performance
(log loss vs reference) is the signal that the model actually got worse. It arrives late,
because a day's label exists only after that day closes, and it needs weeks of data.

## Consequences

- Slow rolling features (30-day volatility, distance to the 50-day SMA) compare one recent
  regime with a 13-year training average, so they show large, significant PSI almost
  permanently. This is a real difference, not noise, but it is low-information. Per-feature
  alerts can fire as a batch of 7.
- `monitoring_metrics` grows by about 30 rows per day per production model. Retention is
  unbounded for now.
- Prometheus keeps 15 days of data. The Grafana admin password comes from `.env`, and the DB
  datasource uses the application user (a read-only role is recommended in production).
- New dependencies: prometheus-client, sentry-sdk; scipy and httpx moved to runtime
  dependencies.

## Alternatives considered

- **Alert on PSI > 0.25 alone**: alerts on noise in a third of runs with 30-day windows (see
  above).
- **Evidently / whylogs**: heavy dependencies for three statistics we can test exactly. They
  could be adopted later for reports.
- **Alertmanager for routing and dedup**: our alerts come from batch jobs whose evidence lives
  in Postgres. A small cooldown table is enough, and alert history is auditable in SQL. Prometheus
  alert rules can be added later for API/worker SLOs.
- **Retrain on drift**: rejected (see above).
