"""Naive baselines: the bar every real model must clear.

They use no features at all. If a model with 12 features can't beat "predict tomorrow's return
is zero" or "always say up", its features carry no usable information, however impressive its
accuracy looks in isolation. In efficient markets that is the normal outcome.
"""

from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.base import BaseEstimator, ClassifierMixin, RegressorMixin
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.pipeline import Pipeline

from marketlens.ml.models.base import BaseModel, register_model


class ZeroReturnRegressor(RegressorMixin, BaseEstimator):  # type: ignore[misc]
    """Predicts a return of exactly 0 (a random walk's best guess)."""

    def fit(self, X: Any, y: Any) -> "ZeroReturnRegressor":
        self.n_features_in_ = np.asarray(X).shape[1]
        return self

    def predict(self, X: Any) -> np.ndarray:
        return np.zeros(len(X))


class AlwaysUpClassifier(ClassifierMixin, BaseEstimator):  # type: ignore[misc]
    """Says 'up' every day with full confidence: P(up) = 1.

    Fine for accuracy (it scores the share of up days), but certainty is punished hard by
    log loss: every down day costs ln(1/1e-15) ≈ 34.5. That contrast is a lesson in itself.
    """

    def fit(self, X: Any, y: Any) -> "AlwaysUpClassifier":
        self.classes_ = np.array([0.0, 1.0])
        self.n_features_in_ = np.asarray(X).shape[1]
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        return np.tile([0.0, 1.0], (len(X), 1))

    def predict(self, X: Any) -> np.ndarray:
        return np.ones(len(X))


class _Baseline(BaseModel):
    family = "baseline"
    is_baseline = True


@register_model
class ZeroReturn(_Baseline):
    name = "zero_return"
    task = "regression"

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", ZeroReturnRegressor())])


@register_model
class HistoricalMean(_Baseline):
    """The average daily return of the training window (BTC's positive drift)."""

    name = "historical_mean"
    task = "regression"

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", DummyRegressor(strategy="mean"))])


@register_model
class AlwaysUp(_Baseline):
    name = "always_up"
    task = "classification"

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", AlwaysUpClassifier())])


@register_model
class HistoricalUpRate(_Baseline):
    """P(up) = share of up days in the training window. The honest probabilistic baseline."""

    name = "historical_up_rate"
    task = "classification"

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", DummyClassifier(strategy="prior"))])
