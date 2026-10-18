"""Built-in models are imported here so they register themselves on package import."""

from marketlens.ml.models import baseline, boosting, forest, linear  # noqa: F401
from marketlens.ml.models.base import MODELS, BaseModel, get_model, models_for_task, register_model

__all__ = ["MODELS", "BaseModel", "get_model", "models_for_task", "register_model"]
