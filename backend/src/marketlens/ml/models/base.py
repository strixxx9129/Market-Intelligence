"""Model plugin interface and registry.

A model is a recipe for an sklearn Pipeline. Everything that learns from data (scalers,
encoders, the estimator) lives INSIDE the pipeline, so it is fitted on each training fold only
and never sees the test fold (rule 1). Training, evaluation, MLflow logging and model
versioning are shared, so adding a model means writing one small class:

    @register_model
    class MyModel(BaseModel):
        name = "my_model"
        task = "classification"
        family = "linear"
        default_params = {"C": 1.0}

        def build_pipeline(self, params, *, seed, categorical=()):
            return Pipeline([("prep", linear_preprocessor(categorical)),
                             ("model", LogisticRegression(**params, random_state=seed))])
"""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, ClassVar, Literal

from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

Task = Literal["regression", "classification"]
TASKS: tuple[Task, ...] = ("regression", "classification")


class BaseModel(ABC):
    name: ClassVar[str]
    task: ClassVar[Task]
    family: ClassVar[str]  # baseline | linear | forest | boosting | ...
    is_baseline: ClassVar[bool] = False
    default_params: ClassVar[dict[str, Any]] = {}

    def params(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        return {**self.default_params, **(overrides or {})}

    @abstractmethod
    def build_pipeline(
        self, params: dict[str, Any], *, seed: int, categorical: Sequence[str] = ()
    ) -> Pipeline:
        """Return an UNFITTED pipeline. Called once per fold, so nothing leaks across folds."""


def linear_preprocessor(categorical: Sequence[str]) -> ColumnTransformer:
    """Standardise numeric features, one-hot categorical ones (e.g. day_of_week).

    Inside the pipeline this is fitted per training fold: the scaler's mean/std come from the
    past only. Fitting it on the full dataset first would leak future distribution into
    every fold.
    """
    return ColumnTransformer(
        [("categorical", OneHotEncoder(handle_unknown="ignore"), list(categorical))],
        remainder=StandardScaler(),
        verbose_feature_names_out=False,
    )


MODELS: dict[str, type[BaseModel]] = {}


def register_model[M: type[BaseModel]](cls: M) -> M:
    existing = MODELS.get(cls.name)
    if existing is not None and existing is not cls:
        raise ValueError(f"model {cls.name!r} already registered by {existing!r}")
    if cls.task not in TASKS:
        raise ValueError(f"model {cls.name!r} has unknown task {cls.task!r}")
    MODELS[cls.name] = cls
    return cls


def get_model(name: str) -> BaseModel:
    try:
        return MODELS[name]()
    except KeyError:
        raise KeyError(f"unknown model {name!r}; available: {sorted(MODELS)}") from None


def models_for_task(task: Task) -> list[BaseModel]:
    """Baselines first, then everything else, each group sorted by name (stable reports)."""
    classes = sorted(
        (c for c in MODELS.values() if c.task == task), key=lambda c: (not c.is_baseline, c.name)
    )
    return [c() for c in classes]
