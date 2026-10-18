"""Named, versioned feature sets.

A version is a frozen list. Never edit an existing entry: add "v2" instead. The builder
refuses to rebuild a version whose feature code changed (see `FeatureSet.code_hash`).
"""

FEATURE_SETS: dict[str, tuple[str, ...]] = {
    "v1": (
        "log_return_1d",
        "log_return_7d",
        "log_return_30d",
        "volatility_7d",
        "volatility_30d",
        "volatility_ratio_7_30",
        "close_to_sma_20",
        "close_to_sma_50",
        "range_pct",
        "volume_zscore_30d",
        "volume_change_1d",
        "day_of_week",
    ),
}
