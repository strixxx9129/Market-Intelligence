"""Random forests with conservative depth.

Trees can model non-linear interactions (e.g. "momentum only matters when volatility is low").
They also memorise noise easily, so depth is capped (max_depth=4) and every leaf must hold at
least 50 days. Trees need no scaling (splits are rank-based), so there is no preprocessing.
"""

from collections.abc import Sequence
from typing import Any, ClassVar

from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.pipeline import Pipeline

from marketlens.ml.models.base import BaseModel, register_model

_FOREST_DEFAULTS: dict[str, Any] = {
    "n_estimators": 300,
    "max_depth": 4,
    "min_samples_leaf": 50,
    "max_features": "sqrt",
    "n_jobs": 2,
}


@register_model
class RandomForestReg(BaseModel):
    name = "random_forest_reg"
    task = "regression"
    family = "forest"
    default_params: ClassVar[dict[str, Any]] = dict(_FOREST_DEFAULTS)

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", RandomForestRegressor(**params, random_state=seed))])


@register_model
class RandomForestClf(BaseModel):
    name = "random_forest_clf"
    task = "classification"
    family = "forest"
    default_params: ClassVar[dict[str, Any]] = dict(_FOREST_DEFAULTS)

    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        return Pipeline([("model", RandomForestClassifier(**params, random_state=seed))])
