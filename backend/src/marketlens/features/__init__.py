"""Feature modules are imported here so every feature registers itself on package import.

`targets` is intentionally NOT imported: features must never depend on the future.
"""

from marketlens.features import (  # noqa: F401
    calendar,
    momentum,
    range,
    returns,
    volatility,
    volume,
)
from marketlens.features.registry import FEATURES, FeatureSpec, register_feature

__all__ = ["FEATURES", "FeatureSpec", "register_feature"]
