"""Assemble the modelling dataset: features (row t) + target (t → t+1), honestly aligned.

Features come from the `features` table (built and leakage-tested in Phase 4). Targets are
computed here from `market_prices` with `features.targets`. They are never stored next to
features, so they can't be picked up as inputs by accident.
"""

import hashlib
from dataclasses import dataclass

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.features.targets import next_day_log_return, next_day_up
from marketlens.ml.models.base import Task
from marketlens.models import FeatureSet
from marketlens.repositories import feature_repo, ingestion_repo, price_repo

CATEGORICAL_FEATURES = ("day_of_week",)


@dataclass
class Dataset:
    X: pd.DataFrame
    y: pd.Series
    task: Task
    feature_set: FeatureSet
    asset_id: int
    dataset_hash: str

    @property
    def categorical(self) -> list[str]:
        return [c for c in CATEGORICAL_FEATURES if c in self.X.columns]


def assemble(
    features: pd.DataFrame,
    candles: pd.DataFrame,
    task: Task,
    feature_names: list[str],
    start: str | None = None,
) -> tuple[pd.DataFrame, pd.Series]:
    target = next_day_log_return(candles) if task == "regression" else next_day_up(candles)
    frame = features[feature_names].join(target.rename("target"), how="inner")
    if start:
        frame = frame.loc[pd.Timestamp(start, tz="UTC") :]
    frame = frame.dropna(subset=["target"])  # the last day has no future yet
    return frame[feature_names], frame["target"]


def dataset_hash(X: pd.DataFrame, y: pd.Series) -> str:
    """Content hash of exactly what the models see (values, dates, column order)."""
    digest = hashlib.sha256(",".join(X.columns).encode())
    digest.update(pd.util.hash_pandas_object(X, index=True).to_numpy().tobytes())
    digest.update(pd.util.hash_pandas_object(y, index=True).to_numpy().tobytes())
    return digest.hexdigest()


async def load_dataset(
    session: AsyncSession,
    feature_set: str,
    task: Task,
    asset: str = "BTC-USD",
    interval: str = "1d",
    start: str | None = "2013-01-01",
) -> Dataset:
    asset_id = await ingestion_repo.get_asset_id(session, asset)
    if asset_id is None:
        raise LookupError(f"asset {asset!r} not found; run `marketlens seed-assets`")
    fs = await feature_repo.get_feature_set(session, feature_set)
    if fs is None:
        raise LookupError(
            f"feature set {feature_set!r} not built; run `marketlens build-features "
            f"--feature-set {feature_set}`"
        )
    features = await feature_repo.load_features(session, asset_id, fs.id)
    candles = await price_repo.load_candles(session, asset_id, interval)
    names = [f["name"] for f in fs.definition["features"]]
    X, y = assemble(features, candles, task, names, start)
    if X.empty:
        raise ValueError("dataset is empty after alignment; check data and start date")
    return Dataset(X, y, task, fs, asset_id, dataset_hash(X, y))
