"""Regularised linear models: few parameters, hard to overfit, easy to interpret.

Strong L2 regularisation (Ridge alpha=10, LogisticRegression C=0.1) shrinks coefficients
towards zero. With a weak signal and fat-tailed noise (Phase 2), an unregularised model
would chase the noise.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline

from marketlens.ml.models.base import BaseModel, linear_preprocessor, register_model


@register_model
class RidgeRegression(BaseModel):
    name = "ridge"
    task = "regression"
    family = "linear"
    default_params: ClassVar[dict[str, Any]] = {"alpha": 10.0}

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("prep", linear_preprocessor(categorical)), ("model", Ridge(**params))])


@register_model
class Logistic(BaseModel):
    name = "logistic"
    task = "classification"
    family = "linear"
    default_params: ClassVar[dict[str, Any]] = {"C": 0.1, "max_iter": 2000}

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline(
            [
                ("prep", linear_preprocessor(categorical)),
                ("model", LogisticRegression(**params, random_state=seed)),
            ]
        )
