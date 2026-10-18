"""Out-of-sample evaluation.

Regression:      MAE (robust to fat tails, primary), RMSE, directional accuracy.
Classification:  log loss (primary), Brier score, ROC-AUC, accuracy, calibration table.

Why not just accuracy? BTC rose on ~52% of days, so "always up" already scores ~52% with zero
skill, and accuracy ignores HOW confident a prediction was. Log loss and Brier score reward
probabilities that are honest: saying 0.55 when it's really a coin flip is cheap, saying 0.95
and being wrong is very expensive. ROC-AUC asks only "are up-days ranked above down-days?"
(0.5 = no ranking skill).

Every model is compared with the BEST baseline for each metric, fold by fold.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, roc_auc_score
from sklearn.pipeline import Pipeline

EPS = 1e-15
LEAK_SHARE = 0.5  # one feature carrying > 50% of all positive importance ...
MATERIAL_FRACTION = 0.01  # ... AND shuffling it worsens the score by >= 1% is suspicious

# For each metric: is lower or higher better?
METRIC_DIRECTION: dict[str, Literal["min", "max"]] = {
    "mae": "min",
    "rmse": "min",
    "directional_accuracy": "max",
    "log_loss": "min",
    "brier": "min",
    "roc_auc": "max",
    "accuracy": "max",
}
PRIMARY_METRIC = {"regression": "mae", "classification": "log_loss"}
IMPORTANCE_SCORING = {"regression": "neg_mean_absolute_error", "classification": "neg_log_loss"}


# ---------------------------------------------------------------- metrics
def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
        # "Up" means > 0 for both; a prediction of exactly 0 counts as "not up".
        "directional_accuracy": float(np.mean((y_pred > 0) == (y_true > 0))),
    }


def classification_metrics(y_true: np.ndarray, proba: np.ndarray) -> dict[str, float]:
    y_true = np.asarray(y_true, float)
    p = np.clip(np.asarray(proba, float), EPS, 1 - EPS)
    both_classes = len(np.unique(y_true)) == 2
    return {
        "log_loss": float(log_loss(y_true, p, labels=[0.0, 1.0])),
        "brier": float(brier_score_loss(y_true, p)),
        "roc_auc": float(roc_auc_score(y_true, p)) if both_classes else float("nan"),
        "accuracy": float(np.mean((p > 0.5) == (y_true == 1))),
    }


def calibration_table(
    y_true: np.ndarray, proba: np.ndarray, bins: int = 10
) -> list[dict[str, float]]:
    """Group predictions into quantile bins of predicted P(up); compare with what happened.

    Well calibrated: observed_rate ≈ mean_pred in every bin. Quantile bins (equal counts)
    are used because daily-return models predict in a narrow band around 0.5; fixed-width bins
    would put almost everything into the 0.5-0.6 bin.
    """
    frame = pd.DataFrame({"y": np.asarray(y_true, float), "p": np.asarray(proba, float)})
    if frame["p"].nunique() < 2:
        groups = [frame]
    else:
        codes = pd.qcut(frame["p"], q=min(bins, frame["p"].nunique()), duplicates="drop")
        groups = [g for _, g in frame.groupby(codes, observed=True)]
    return [
        {
            "bin": i,
            "n": len(g),
            "mean_pred": float(g["p"].mean()),
            "observed_rate": float(g["y"].mean()),
            "gap": float(g["y"].mean() - g["p"].mean()),
        }
        for i, g in enumerate(groups)
    ]


def compute_metrics(
    task: str, y_true: np.ndarray, pred: np.ndarray, proba: np.ndarray | None
) -> dict[str, float]:
    if task == "regression":
        return regression_metrics(y_true, pred)
    assert proba is not None
    return classification_metrics(y_true, proba)


# ---------------------------------------------------------------- aggregation
def summarize(per_fold: pd.DataFrame) -> dict[str, dict[str, float]]:
    """{metric: {mean, std}} over folds (std with ddof=1: spread between folds)."""
    return {
        m: {"mean": float(per_fold[m].mean()), "std": float(per_fold[m].std(ddof=1))}
        for m in per_fold.columns
    }


@dataclass
class BaselineComparison:
    metric: str
    baseline: str
    diff_mean: float  # model - baseline (sign as the raw metric)
    diff_std: float
    improvement_mean: float  # positive = model better, whatever the metric direction
    wins: int
    folds: int
    significant: bool  # better on average by > 2 standard errors AND wins most folds

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def compare_to_baselines(
    per_fold: dict[str, pd.DataFrame], baselines: Sequence[str]
) -> dict[str, dict[str, BaselineComparison]]:
    """For every model and metric: difference vs the best baseline for THAT metric.

    The best baseline is chosen by its mean over folds (e.g. historical_up_rate wins log loss,
    always_up wins accuracy). `wins` = folds where the model beat it.
    """
    out: dict[str, dict[str, BaselineComparison]] = {}
    metrics = next(iter(per_fold.values())).columns
    for metric in metrics:
        sign = -1.0 if METRIC_DIRECTION[metric] == "min" else 1.0
        valid = [b for b in baselines if per_fold[b][metric].notna().all()]
        if not valid:
            continue
        best = max(valid, key=lambda b: sign * per_fold[b][metric].mean())
        for model, frame in per_fold.items():
            if model in baselines:
                continue
            diff = frame[metric].to_numpy() - per_fold[best][metric].to_numpy()
            improvement = sign * diff
            n = int(np.sum(~np.isnan(improvement)))
            se = np.nanstd(improvement, ddof=1) / np.sqrt(n) if n > 1 else np.inf
            wins = int(np.nansum(improvement > 0))
            out.setdefault(model, {})[metric] = BaselineComparison(
                metric=metric,
                baseline=best,
                diff_mean=float(np.nanmean(diff)),
                diff_std=float(np.nanstd(diff, ddof=1)) if n > 1 else float("nan"),
                improvement_mean=float(np.nanmean(improvement)),
                wins=wins,
                folds=n,
                significant=bool(np.nanmean(improvement) > 2 * se and wins > n / 2),
            )
    return out


def era_metrics(
    task: str, oos: pd.DataFrame, eras: dict[str, tuple[str, str]]
) -> dict[str, dict[str, float]]:
    """Metrics on pooled out-of-sample predictions, per market era (regimes, Phase 2)."""
    out: dict[str, dict[str, float]] = {}
    for name, (start, end) in eras.items():
        part = oos.loc[start:end]
        if len(part) < 30:
            continue
        proba = part["proba"].to_numpy() if task == "classification" else None
        out[name] = {
            **compute_metrics(task, part["y_true"].to_numpy(), part["y_pred"].to_numpy(), proba),
            "n": float(len(part)),
        }
    return out


# ---------------------------------------------------------------- permutation importance
def fold_permutation_importance(
    pipeline: Pipeline,
    X_test: pd.DataFrame,
    y_test: np.ndarray,
    task: str,
    seed: int,
    n_repeats: int = 5,
) -> pd.Series:
    """How much does the test score drop when one feature's values are shuffled?

    Computed on the TEST fold, so it measures what the model actually uses on unseen data.
    A drop near 0 = the model doesn't rely on it; negative = it was hurting.
    """
    result = permutation_importance(
        pipeline,
        X_test,
        y_test,
        scoring=IMPORTANCE_SCORING[task],
        n_repeats=n_repeats,
        random_state=seed,
        n_jobs=1,
    )
    return pd.Series(result.importances_mean, index=X_test.columns)


def importance_summary(
    per_fold: list[pd.Series], reference_score: float | None = None
) -> dict[str, Any]:
    """Average importance over folds; flag dominant AND material features as possible leaks.

    A leak looks like one feature carrying most of the model's skill, and that skill being
    large: shuffling the feature wrecks the score. Dominance alone is not enough. A model with
    almost no skill always has *some* feature holding most of almost nothing (seen on BTC: one
    feature with 75% of a 0.2% effect). So a flag needs both:
      - share > 50% of all positive importance, and
      - mean score drop >= 1% of the model's own score (`reference_score`).
    Dominant-but-immaterial features are listed separately as information.
    Flags mean "investigate", not "proven leak".
    """
    frame = pd.concat(per_fold, axis=1)
    mean = frame.mean(axis=1)
    positive = mean.clip(lower=0)
    total = float(positive.sum())
    share = positive / total if total > 0 else positive * 0
    material = (
        (lambda v: v >= MATERIAL_FRACTION * abs(reference_score))
        if reference_score
        else (lambda v: True)
    )
    flags: list[dict[str, Any]] = []
    dominant: list[dict[str, Any]] = []
    for f in share.index:
        if total > 0 and share[f] > LEAK_SHARE:
            item = {
                "feature": f,
                "share": round(float(share[f]), 4),
                "importance": float(mean[f]),
                "relative_to_score": float(mean[f] / abs(reference_score))
                if reference_score
                else None,
            }
            (flags if material(float(mean[f])) else dominant).append(item)
    # NB: JSONB does not keep key order, so consumers must sort `mean` themselves.
    return {
        "mean": {k: float(v) for k, v in mean.items()},
        "std": {k: float(v) for k, v in frame.std(axis=1, ddof=1).items()},
        "share": {k: float(v) for k, v in share.items()},
        "total_positive": total,
        "reference_score": reference_score,
        "leak_flags": flags,
        "dominant_immaterial": dominant,
    }
