import numpy as np
import pandas as pd
import pytest
from sklearn.base import clone

from marketlens.ml.models import MODELS, get_model, models_for_task
from marketlens.ml.models import base as base_module
from marketlens.ml.models.base import BaseModel, register_model
from marketlens.ml.models.boosting import EarlyStoppingLGBMClassifier

FEATURES = ["f1", "f2", "f3", "day_of_week"]


@pytest.fixture(scope="module")
def data() -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    rng = np.random.default_rng(0)
    n = 600
    X = pd.DataFrame(rng.normal(size=(n, 3)), columns=FEATURES[:3])
    X["day_of_week"] = np.arange(n) % 7
    signal = 0.5 * X["f1"] - 0.3 * X["f2"]
    y_reg = pd.Series(0.01 * signal + rng.normal(0, 0.01, n))
    y_clf = (y_reg > 0).astype(float)
    return X, y_reg, y_clf


def test_registry_has_both_tasks_with_baselines_first() -> None:
    for task in ("regression", "classification"):
        models = models_for_task(task)  # type: ignore[arg-type]
        assert len(models) == 5
        assert [m.is_baseline for m in models[:2]] == [True, True]
        assert not any(m.is_baseline for m in models[2:])
    assert {m.name for m in models_for_task("classification")} == {
        "always_up",
        "historical_up_rate",
        "logistic",
        "random_forest_clf",
        "lightgbm_clf",
    }


@pytest.mark.parametrize("name", sorted(MODELS))
def test_every_model_fits_and_predicts(
    name: str, data: tuple[pd.DataFrame, pd.Series, pd.Series]
) -> None:
    X, y_reg, y_clf = data
    model = get_model(name)
    y = y_reg if model.task == "regression" else y_clf
    pipe = model.build_pipeline(model.params(), seed=1, categorical=["day_of_week"])
    pipe.fit(X.iloc[:500], y.iloc[:500])
    pred = pipe.predict(X.iloc[500:])
    assert pred.shape == (100,)
    if model.task == "classification":
        proba = pipe.predict_proba(X.iloc[500:])
        assert proba.shape == (100, 2) and np.allclose(proba.sum(axis=1), 1)
    clone(pipe)  # sklearn-compatible: params can be cloned for every fold


def test_baselines_predict_what_they_promise(
    data: tuple[pd.DataFrame, pd.Series, pd.Series],
) -> None:
    X, y_reg, y_clf = data
    fit = lambda name, y: get_model(name).build_pipeline({}, seed=0).fit(X, y)  # noqa: E731
    assert np.all(fit("zero_return", y_reg).predict(X) == 0)
    assert np.allclose(fit("historical_mean", y_reg).predict(X), y_reg.mean())
    assert np.all(fit("always_up", y_clf).predict_proba(X)[:, 1] == 1.0)
    assert np.allclose(fit("historical_up_rate", y_clf).predict_proba(X)[:, 1], y_clf.mean())


def test_scaler_is_fitted_on_the_training_fold_only(
    data: tuple[pd.DataFrame, pd.Series, pd.Series],
) -> None:
    X, _, y_clf = data
    shifted = X.copy()
    shifted.loc[300:, "f1"] += 100  # the "future" has a very different distribution
    pipe = get_model("logistic").build_pipeline(
        get_model("logistic").params(), seed=0, categorical=["day_of_week"]
    )
    pipe.fit(shifted.iloc[:300], y_clf.iloc[:300])
    scaler = pipe.named_steps["prep"].named_transformers_["remainder"]
    f1_pos = list(scaler.feature_names_in_).index("f1")
    # Mean learned from the training rows only, not polluted by the shifted future rows.
    assert scaler.mean_[f1_pos] == pytest.approx(shifted["f1"].iloc[:300].mean())
    assert scaler.mean_[f1_pos] < 1


def test_lightgbm_early_stops_on_the_training_tail(
    data: tuple[pd.DataFrame, pd.Series, pd.Series],
) -> None:
    X, _, y_clf = data
    est = EarlyStoppingLGBMClassifier(random_state=0, n_estimators=2000)
    est.fit(X.iloc[:400], y_clf.iloc[:400])
    val = est.validation_index_
    assert val.max() == 399  # most recent training rows ...
    assert len(val) == 60 and val.min() > 400 - 60 - 5 - 1  # ... after the inner gap
    assert est.best_iteration_ < 2000  # it actually stopped early


def test_register_model_rejects_duplicates_and_bad_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base_module, "MODELS", dict(MODELS))

    class Dup(BaseModel):
        name = "logistic"
        task = "classification"
        family = "x"

        def build_pipeline(self, params, *, seed, categorical=()):  # type: ignore[no-untyped-def]
            raise NotImplementedError

    with pytest.raises(ValueError, match="already registered"):
        register_model(Dup)

    class BadTask(Dup):
        name = "bad_task"
        task = "ranking"  # type: ignore[assignment]

    with pytest.raises(ValueError, match="unknown task"):
        register_model(BadTask)
    with pytest.raises(KeyError, match="unknown model"):
        get_model("nope")
