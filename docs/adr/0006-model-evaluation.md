# ADR 0006: Model training and honest evaluation

- Status: Accepted
- Date: 2026-10-19

## Context

Financial ML is dominated by false positives: random splits, tuning on the test set, accuracy
compared against nothing, and features that leak. MarketLens must make it hard to fool
ourselves, and must make "no model beats the baseline" an acceptable, clearly reported outcome.

## Decision

1. **Walk-forward validation only**, with an embargo gap (default 5 days; ≥ label horizon),
   expanding or rolling windows (`ml/splits.py`). A property test checks, for 120 split configs,
   that every test index > every train index + gap. Default: 730-day initial train, 180-day test
   folds → ~23 folds on BTC 2013+.
2. **Identical folds for every model**, with **feature-free baselines** (zero return, historical
   mean; always-up, historical up-rate) trained and scored like any model. Every metric is
   reported as the difference vs the *best baseline for that metric*, with fold wins.
3. **"Beats the baseline"** = mean improvement > 2 standard errors of the per-fold differences
   AND better in more than half of the folds. The verdict is computed, never hand-written.
4. **Primary metrics** that respect fat tails and probabilities: MAE for regression, log loss
   for classification. Accuracy and directional accuracy are reported but never decisive.
   Calibration tables (quantile bins) for classifiers.
5. **Everything learned is inside an sklearn Pipeline** built fresh per fold (scaler, one-hot,
   estimator). LightGBM early-stops on the most recent 15% of the *training* fold (after a gap)
   via a wrapper estimator; the test fold is never used to make a fitting decision.
6. **No hyperparameter tuning on the reported folds.** Fixed, conservative defaults (strong L2,
   depth ≤ 4, ≥ 50 samples per leaf). Tuning, if added, must be nested inside the training folds.
7. **Permutation importance on test folds**; a feature with > 50% of the positive importance is
   flagged as a possible leak (investigate, not proof).
8. **Reproducibility & storage.** `training_runs` (split config, dataset hash, feature set +
   code hash, git SHA incl. `-dirty`, seed, metrics JSON), `model_versions` (immutable, enforced
   by a DB trigger that only allows `stage` updates), `oos_predictions` (for the backtest).
   MLflow: parent run per training run, nested run per model with per-fold metrics, the final
   all-data model (joblib) and OOS predictions as artifacts. `mlflow-skinny` client in the
   backend; the full server image is unchanged.
9. **Generated report** (`docs/experiment-report.md`) from the DB, never edited by hand.

## Consequences

- Results are slower to look good and quicker to look honest; expected outcome for next-day
  BTC direction is "near the baseline".
- The final model of each version is trained on all data; its out-of-sample quality is the
  walk-forward estimate, not a score on the data it was fitted to.
- Model versions accumulate (one per model per run); stage transitions (candidate → staging →
  production) are the only allowed mutation and will be driven by later phases.
- Tests use a local MLflow file store (`MLFLOW_ALLOW_FILE_STORE=true`, test-only).
