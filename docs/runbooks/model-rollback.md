# Runbook: roll back the production model

**When:** live log loss alert (`log_loss_30d` worse than the walk-forward reference), a
promotion that looks wrong, or forecasts that look broken (e.g. P(up) stuck at 0 or 1).

Model versions are **immutable** (a database trigger forbids changing anything but `stage`),
so a rollback never rebuilds anything: it moves the `production` stage back to a version that
already exists, with its artifacts, metrics and training run intact.

## 1. Look before acting

```bash
fly ssh console -a marketlens-api -C "marketlens model-versions --limit 15"
```
- Website `/models`: the leaderboard and each version's card (folds vs baseline, promotion
  history).
- `/api/v1/status` and the Grafana "Live log loss vs reference" panel: is it really worse, on
  enough scored days (n ≥ 20)? Labels arrive a day late; one bad week is often noise.
- A drift alert alone is **not** a reason to roll back (see ADR 0010): check the inputs first.

## 2. Roll back

Promote the previous version through the same gate the weekly job uses:
```bash
fly ssh console -a marketlens-api -C "marketlens admin promote <previous model_version id>"
```
- `promoted`: done. The old production version is `archived`; the decision is recorded in
  `promotion_decisions` with the actor.
- `rejected` (the previous version doesn't pass the gate either): fall back to the **baseline**
  of the same training run, which is always a safe, honest choice:
  ```bash
  fly ssh console -a marketlens-api -C \
    "marketlens admin promote <baseline id> --force --reason 'rollback: <incident>'"
  ```
  `--force` requires a reason and is audited as a `manual` decision.

The API equivalent (admin token): `POST /api/v1/admin/models/{id}/promote` with
`{"force": true, "reason": "..."}`.

## 3. Make the change visible

Promotion already invalidates the `models`, `forecast` and `status` caches. The next
`predict` (00:15 UTC) uses the new production model; to re-forecast now:
```bash
fly ssh console -a marketlens-api -C "marketlens admin run-daily"
```

## 4. Stop it happening again

The weekly job promotes only through the gate. If a bad model passed it, the gate needs
tightening (`PROMOTION_MIN_LOGLOSS_IMPROVEMENT`, `PROMOTION_MAX_WORSE_FOLDS`), with a test
for the case that slipped through. Don't disable weekly training to "freeze" a model: a
frozen model silently ages.

## Rolling back the CODE (not the model)

Every image is tagged with its commit SHA. Redeploy the previous one:
```bash
fly deploy --config infra/fly/api.production.toml --image ghcr.io/<owner>/marketlens-backend:<previous sha>
```
Migrations are forward-only and written to stay compatible with the previous release
(expand → migrate → contract), so the old code runs on the new schema.
