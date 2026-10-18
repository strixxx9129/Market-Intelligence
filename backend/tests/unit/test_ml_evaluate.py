import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from marketlens.ml.evaluate import (
    calibration_table,
    classification_metrics,
    compare_to_baselines,
    era_metrics,
    fold_permutation_importance,
    importance_summary,
    regression_metrics,
    summarize,
)


def test_regression_metrics_by_hand() -> None:
    y = np.array([0.02, -0.01, 0.03, -0.04])
    p = np.array([0.01, 0.01, -0.01, -0.02])
    m = regression_metrics(y, p)
    assert m["mae"] == pytest.approx((0.01 + 0.02 + 0.04 + 0.02) / 4)
    assert m["rmse"] == pytest.approx(np.sqrt((0.01**2 + 0.02**2 + 0.04**2 + 0.02**2) / 4))
    assert m["directional_accuracy"] == pytest.approx(0.5)  # right on 1st and 4th
    assert regression_metrics(y, np.zeros(4))["directional_accuracy"] == 0.5  # 0 = "not up"


def test_classification_metrics_by_hand() -> None:
    y = np.array([1.0, 0.0, 1.0, 0.0])
    p = np.array([0.8, 0.3, 0.6, 0.6])
    m = classification_metrics(y, p)
    expected_ll = -np.mean(np.log([0.8, 0.7, 0.6, 0.4]))
    assert m["log_loss"] == pytest.approx(expected_ll)
    assert m["brier"] == pytest.approx(np.mean([0.04, 0.09, 0.16, 0.36]))
    assert m["accuracy"] == pytest.approx(0.75)
    assert m["roc_auc"] == pytest.approx(0.875)


def test_overconfidence_is_punished_by_log_loss_not_accuracy() -> None:
    y = np.array([1, 1, 1, 0] * 25, dtype=float)
    always_up = classification_metrics(y, np.ones(100))
    prior = classification_metrics(y, np.full(100, 0.75))
    assert always_up["accuracy"] == prior["accuracy"] == 0.75
    assert always_up["log_loss"] > 8 > prior["log_loss"]  # certainty that is sometimes wrong


def test_calibration_table_bins_and_gaps() -> None:
    rng = np.random.default_rng(0)
    p = rng.uniform(0.3, 0.7, 5000)
    y = (rng.uniform(size=5000) < p).astype(float)  # perfectly calibrated by construction
    table = calibration_table(y, p, bins=5)
    assert len(table) == 5 and sum(r["n"] for r in table) == 5000
    assert all(abs(r["gap"]) < 0.05 for r in table)
    assert [r["mean_pred"] for r in table] == sorted(r["mean_pred"] for r in table)
    assert len(calibration_table(y, np.full(5000, 0.5))) == 1  # constant prediction: one bin


def test_summarize_and_compare_to_best_baseline() -> None:
    folds = {
        "always_up": pd.DataFrame({"log_loss": [20.0, 21.0, 19.0], "accuracy": [0.55, 0.52, 0.53]}),
        "prior": pd.DataFrame({"log_loss": [0.69, 0.70, 0.69], "accuracy": [0.55, 0.52, 0.53]}),
        "model": pd.DataFrame({"log_loss": [0.68, 0.71, 0.68], "accuracy": [0.56, 0.50, 0.54]}),
    }
    s = summarize(folds["model"])
    assert s["log_loss"]["mean"] == pytest.approx(0.69)
    cmp = compare_to_baselines(folds, ["always_up", "prior"])["model"]
    assert cmp["log_loss"].baseline == "prior"  # best baseline chosen per metric
    assert cmp["log_loss"].wins == 2 and cmp["log_loss"].folds == 3
    assert cmp["log_loss"].improvement_mean == pytest.approx((0.01 - 0.01 + 0.01) / 3)
    assert not cmp["log_loss"].significant  # tiny, noisy improvement is not "beating" it
    assert cmp["accuracy"].baseline in {"always_up", "prior"}


def test_clear_improvement_is_significant() -> None:
    folds = {
        "base": pd.DataFrame({"mae": [1.0] * 10}),
        "model": pd.DataFrame({"mae": [0.9 + 0.001 * i for i in range(10)]}),
    }
    assert compare_to_baselines(folds, ["base"])["model"]["mae"].significant


def test_era_metrics_split_pooled_predictions() -> None:
    idx = pd.date_range("2015-01-01", "2022-12-31", freq="1D", tz="UTC")
    oos = pd.DataFrame({"y_true": 0.01, "y_pred": 0.005, "proba": np.nan}, index=idx)
    eras = era_metrics(
        "regression", oos, {"a": ("2015", "2018"), "b": ("2019", "2022"), "empty": ("2030", "2031")}
    )
    assert set(eras) == {"a", "b"}
    assert eras["a"]["mae"] == pytest.approx(0.005)


def test_a_leaky_feature_dominates_importance_and_is_flagged() -> None:
    rng = np.random.default_rng(1)
    n = 800
    y = (rng.uniform(size=n) > 0.5).astype(float)
    X = pd.DataFrame(
        {
            "noise_a": rng.normal(size=n),
            "noise_b": rng.normal(size=n),
            "leak": y + rng.normal(0, 0.1, n),
        }
    )  # secretly contains the label
    pipe = Pipeline([("model", LogisticRegression())]).fit(X.iloc[:600], y[:600])
    per_fold = [fold_permutation_importance(pipe, X.iloc[600:], y[600:], "classification", 0)]
    summary = importance_summary(per_fold, reference_score=0.69)
    assert [f["feature"] for f in summary["leak_flags"]] == ["leak"]
    assert max(summary["mean"], key=summary["mean"].get) == "leak"


def test_dominant_but_tiny_importance_is_not_a_leak_flag() -> None:
    # What BTC models show: one feature holds most of an effect worth 0.2% of the score.
    s = importance_summary(
        [pd.Series({"a": 0.0014, "b": 0.0004, "c": -0.001})], reference_score=0.69
    )
    assert s["leak_flags"] == []
    assert [d["feature"] for d in s["dominant_immaterial"]] == ["a"]


def test_no_flags_when_nothing_helps() -> None:
    s = importance_summary([pd.Series({"a": -0.001, "b": 0.0})])
    assert s["leak_flags"] == [] and s["total_positive"] == 0
