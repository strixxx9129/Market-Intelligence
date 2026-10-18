"""Build a named feature set for an asset and store it in `features`.

1. Load daily candles from market_prices (oldest → newest).
2. Compute every feature of the set on the full history.
3. Drop the warm-up rows (the first max(lookback) rows). They are DROPPED, never filled:
   back-filling a rolling mean at day 5 with the value from day 20 would leak the future.
   Any NaN/inf left after that is a bug (wrong lookback, division by zero) → fail loudly.
4. Check version immutability: code_hash(definition + feature source) must match the stored
   feature_sets row, or the build is refused ("changed code needs a new version").
5. UPSERT rows (unchanged rows are not rewritten) and record a pipeline_runs row, atomically.
"""

import hashlib
import inspect
import json
import uuid
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.logging import get_logger
from marketlens.features import FEATURES
from marketlens.features.registry import FeatureSpec, check_candles
from marketlens.features.sets import FEATURE_SETS
from marketlens.models import PipelineRunStatus
from marketlens.repositories import feature_repo, ingestion_repo, price_repo

logger = get_logger(__name__)
PIPELINE_NAME = "build_features"


class FeatureSetChangedError(RuntimeError):
    """The code behind an existing feature-set version changed."""


# ---------------------------------------------------------------- pure functions
def resolve_set(version: str) -> list[FeatureSpec]:
    try:
        names = FEATURE_SETS[version]
    except KeyError:
        raise KeyError(f"unknown feature set {version!r}; known: {sorted(FEATURE_SETS)}") from None
    missing = [n for n in names if n not in FEATURES]
    if missing:
        raise KeyError(f"feature set {version!r} references unregistered features: {missing}")
    return [FEATURES[n] for n in names]


def set_definition(version: str, specs: list[FeatureSpec]) -> dict[str, Any]:
    return {
        "version": version,
        "warmup_rows": warmup_rows(specs),
        "features": [
            {
                "name": s.name,
                "family": s.family,
                "lookback": s.lookback,
                "description": s.description,
            }
            for s in specs
        ],
    }


def warmup_rows(specs: list[FeatureSpec]) -> int:
    return max((s.lookback for s in specs), default=0)


def code_hash(definition: dict[str, Any], specs: list[FeatureSpec]) -> str:
    """SHA-256 over the set definition and the full source of every module that defines one of
    its features (whole modules, so edits to shared helpers in that module are detected)."""
    digest = hashlib.sha256(json.dumps(definition, sort_keys=True).encode())
    modules = sorted({s.func.__module__ for s in specs})
    for name in modules:
        module = inspect.getmodule(next(s.func for s in specs if s.func.__module__ == name))
        digest.update(name.encode())
        digest.update(inspect.getsource(module).encode())  # type: ignore[arg-type]
    return digest.hexdigest()


def compute_features(candles: pd.DataFrame, specs: list[FeatureSpec]) -> pd.DataFrame:
    """All features on the full series; warm-up rows still present (NaN)."""
    check_candles(candles)
    return pd.concat([spec.compute(candles) for spec in specs], axis=1)


def drop_warmup(frame: pd.DataFrame, rows: int) -> pd.DataFrame:
    out = frame.iloc[rows:]
    bad = ~np.isfinite(out.to_numpy(dtype="float64"))
    if bad.any():
        r, col = np.argwhere(bad)[0]
        raise ValueError(
            f"non-finite value after warm-up: feature {out.columns[col]!r} at {out.index[r]} "
            f"({int(bad.sum())} in total); check its lookback or a division by zero"
        )
    return out


# ---------------------------------------------------------------- service
@dataclass
class BuildResult:
    run_id: uuid.UUID
    feature_set: str
    feature_set_id: int
    code_hash: str
    candles: int
    warmup_rows: int
    rows: int
    rows_changed: int
    first_ts: pd.Timestamp | None
    last_ts: pd.Timestamp | None


class FeatureBuilder:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self.sessionmaker = sessionmaker

    async def build(
        self, version: str, asset: str = "BTC-USD", interval: str = "1d"
    ) -> BuildResult:
        specs = resolve_set(version)
        definition = set_definition(version, specs)
        digest = code_hash(definition, specs)
        params = {"feature_set": version, "asset": asset, "interval": interval}

        async with self.sessionmaker() as session, session.begin():
            run_id = await ingestion_repo.create_run(session, PIPELINE_NAME, params)
        log = logger.bind(run_id=str(run_id), **params)
        try:
            result = await self._build(run_id, version, asset, interval, specs, definition, digest)
        except Exception as exc:
            async with self.sessionmaker() as session, session.begin():
                await ingestion_repo.finish_run(
                    session, run_id, PipelineRunStatus.FAILED, error=f"{type(exc).__name__}: {exc}"
                )
            log.error("build_features_failed", error=repr(exc))
            raise
        log.info("build_features_finished", rows=result.rows, rows_changed=result.rows_changed)
        return result

    async def _build(
        self,
        run_id: uuid.UUID,
        version: str,
        asset: str,
        interval: str,
        specs: list[FeatureSpec],
        definition: dict[str, Any],
        digest: str,
    ) -> BuildResult:
        async with self.sessionmaker() as session, session.begin():
            asset_id = await ingestion_repo.get_asset_id(session, asset)
            if asset_id is None:
                raise LookupError(f"asset {asset!r} not found; run `marketlens seed-assets`")
            candles = await price_repo.load_candles(session, asset_id, interval)

        warmup = warmup_rows(specs)
        values = drop_warmup(compute_features(candles, specs), warmup)

        async with self.sessionmaker() as session, session.begin():
            fs = await feature_repo.get_feature_set(session, version)
            if fs is not None and fs.code_hash != digest:
                raise FeatureSetChangedError(
                    f"feature set {version!r} was built from different code "
                    f"(stored {fs.code_hash[:12]}…, now {digest[:12]}…). Versions are immutable: "
                    f"add a new version to FEATURE_SETS instead of editing {version!r}."
                )
            if fs is None:
                fs = await feature_repo.create_feature_set(session, version, definition, digest)
            changed = await feature_repo.upsert_features(session, asset_id, fs.id, values)
            await ingestion_repo.finish_run(
                session,
                run_id,
                PipelineRunStatus.SUCCESS,
                rows_processed=len(values),
                extra_params={"code_hash": digest, "rows_changed": changed, "warmup_rows": warmup},
            )

        return BuildResult(
            run_id=run_id,
            feature_set=version,
            feature_set_id=fs.id,
            code_hash=digest,
            candles=len(candles),
            warmup_rows=warmup,
            rows=len(values),
            rows_changed=changed,
            first_ts=values.index[0] if len(values) else None,
            last_ts=values.index[-1] if len(values) else None,
        )
