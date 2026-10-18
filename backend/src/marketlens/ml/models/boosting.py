"""LightGBM with small trees and early stopping on an inner validation slice.

Boosting adds trees one at a time, each correcting the previous ones. Left alone, it keeps
going until it fits the noise. Early stopping watches a validation set and stops when the
error stops improving.

The validation set must NOT be the test fold (that would tune on the test set). Instead the
wrapper below carves the most recent 15% off the TRAINING fold (after a gap), stops on that,
and the test fold stays untouched. Because it all happens inside `fit()`, the wrapper is a
normal sklearn estimator and fits into the shared Pipeline / walk-forward machinery.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin, RegressorMixin
from sklearn.pipeline import Pipeline

from marketlens.ml.models.base import BaseModel, register_model
from marketlens.ml.splits import inner_validation_split

_LGBM_DEFAULTS: dict[str, Any] = {
    "n_estimators": 1000,  # upper bound; early stopping picks the real number
    "learning_rate": 0.02,
    "num_leaves": 7,
    "max_depth": 3,
    "min_child_samples": 50,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_lambda": 1.0,
    "early_stopping_rounds": 50,
    "val_fraction": 0.15,
    "val_gap": 5,
}


class _EarlyStoppingLGBM(BaseEstimator):  # type: ignore[misc]
    _lgbm_class: type

    def __init__(
        self,
        n_estimators: int = 1000,
        learning_rate: float = 0.02,
        num_leaves: int = 7,
        max_depth: int = 3,
        min_child_samples: int = 50,
        subsample: float = 0.8,
        subsample_freq: int = 1,
        colsample_bytree: float = 0.8,
        reg_lambda: float = 1.0,
        early_stopping_rounds: int = 50,
        val_fraction: float = 0.15,
        val_gap: int = 5,
        random_state: int = 0,
    ) -> None:
        self.n_estimators = n_estimators
        self.learning_rate = learning_rate
        self.num_leaves = num_leaves
        self.max_depth = max_depth
        self.min_child_samples = min_child_samples
        self.subsample = subsample
        self.subsample_freq = subsample_freq
        self.colsample_bytree = colsample_bytree
        self.reg_lambda = reg_lambda
        self.early_stopping_rounds = early_stopping_rounds
        self.val_fraction = val_fraction
        self.val_gap = val_gap
        self.random_state = random_state

    def fit(self, X: Any, y: Any) -> "_EarlyStoppingLGBM":
        X = X if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
        y = np.asarray(y)
        fit_idx, val_idx = inner_validation_split(len(X), self.val_fraction, self.val_gap)
        self.model_ = self._lgbm_class(
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            num_leaves=self.num_leaves,
            max_depth=self.max_depth,
            min_child_samples=self.min_child_samples,
            subsample=self.subsample,
            subsample_freq=self.subsample_freq,
            colsample_bytree=self.colsample_bytree,
            reg_lambda=self.reg_lambda,
            random_state=self.random_state,
            n_jobs=2,
            verbose=-1,
        )
        self.model_.fit(
            X.iloc[fit_idx],
            y[fit_idx],
            eval_X=(X.iloc[val_idx],),
            eval_y=(y[val_idx],),
            callbacks=[lgb.early_stopping(self.early_stopping_rounds, verbose=False)],
        )
        self.best_iteration_ = int(self.model_.best_iteration_ or self.n_estimators)
        self.validation_index_ = val_idx  # kept so tests can prove the test fold wasn't used
        self.n_features_in_ = X.shape[1]
        self.feature_names_in_ = np.asarray(X.columns, dtype=object)
        return self

    def predict(self, X: Any) -> np.ndarray:
        return np.asarray(self.model_.predict(X, num_iteration=self.best_iteration_))


class EarlyStoppingLGBMRegressor(RegressorMixin, _EarlyStoppingLGBM):  # type: ignore[misc]
    _lgbm_class = lgb.LGBMRegressor


class EarlyStoppingLGBMClassifier(ClassifierMixin, _EarlyStoppingLGBM):  # type: ignore[misc]
    _lgbm_class = lgb.LGBMClassifier

    def fit(self, X: Any, y: Any) -> "EarlyStoppingLGBMClassifier":
        super().fit(X, y)
        self.classes_ = self.model_.classes_
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        return np.asarray(self.model_.predict_proba(X, num_iteration=self.best_iteration_))


@register_model
class LightGBMReg(BaseModel):
    name = "lightgbm_reg"
    task = "regression"
    family = "boosting"
    default_params: ClassVar[dict[str, Any]] = dict(_LGBM_DEFAULTS)

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", EarlyStoppingLGBMRegressor(**params, random_state=seed))])


@register_model
class LightGBMClf(BaseModel):
    name = "lightgbm_clf"
    task = "classification"
    family = "boosting"
    default_params: ClassVar[dict[str, Any]] = dict(_LGBM_DEFAULTS)

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", EarlyStoppingLGBMClassifier(**params, random_state=seed))])
