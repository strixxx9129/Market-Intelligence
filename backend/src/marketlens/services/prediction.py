"""Forecasting with the production model, and scoring forecasts once the future arrives.

predict:   latest feature row (as_of = day t, features use data <= t) → P(up) for day t+1,
           written with the model_version that produced it. Idempotent UPSERT.
outcomes:  when day t+1's candle exists, compare forecast and reality (log loss, Brier,
           correct). Idempotent UPSERT on prediction_id.

Educational forecasts, not financial advice.
"""

import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import mlflow
import numpy as np
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from marketlens.core.cache import invalidate_namespaces
from marketlens.core.config import get_settings
from marketlens.core.errors import PermanentError
from marketlens.models import FeatureSet, ModelVersion
from marketlens.repositories import feature_repo, ingestion_repo, prediction_repo
from marketlens.services.promotion import PromotionService

HORIZON_DAYS = 1
EPS = 1e-15


def load_model(mv: ModelVersion) -> Any:
    """Fetch the model artifact once per version and cache it on local disk. Versions are
    immutable, so a cached file can never go stale."""
    settings = get_settings()
    target = settings.model_cache_dir / str(mv.id) / "model.joblib"
    if not target.exists():
        if not mv.artifact_uri:
            raise PermanentError(f"model version {mv.id} has no artifact")
        if settings.mlflow_tracking_uri:
            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
        target.parent.mkdir(parents=True, exist_ok=True)
        local = mlflow.artifacts.download_artifacts(
            artifact_uri=mv.artifact_uri, dst_path=str(target.parent)
        )
        if Path(local) != target:
            Path(local).replace(target)
    return joblib.load(target)


class PredictionService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        model_loader: Any = load_model,
    ) -> None:
        self.sessionmaker = sessionmaker
        self.model_loader = model_loader

    async def predict(
        self,
        asset: str,
        start: datetime | None = None,
        end: datetime | None = None,
        source: str = "live",
        pipeline_run_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Live (default): forecast from the newest feature row. Backfill: every feature row
        with start <= as_of <= end, stored with source='backfill'."""
        mv = await PromotionService(self.sessionmaker).ensure_production(asset, pipeline_run_id)
        async with self.sessionmaker() as s:
            asset_id = await ingestion_repo.get_asset_id(s, asset)
            assert asset_id is not None
            fs = await s.get(FeatureSet, mv.feature_set_id)
            assert fs is not None
            features = await feature_repo.load_features(s, asset_id, fs.id)
        if features.empty:
            raise PermanentError("no feature rows: run build-features first")
        names = [f["name"] for f in fs.definition["features"]]
        # Live: only the newest row (as_of = latest complete day). Backfill: the requested range.
        X = features[names].iloc[[-1]] if source == "live" else features[names].loc[start:end]
        if X.empty:
            return {"status": "skipped", "reason": "no feature rows in range", "rows": 0}

        model = self.model_loader(mv)
        p_up = model.predict_proba(X)[:, 1]
        rows = [
            {
                "model_version_id": mv.id,
                "asset_id": asset_id,
                "as_of_ts": ts.to_pydatetime(),
                "target_ts": (ts + timedelta(days=HORIZON_DAYS)).to_pydatetime(),
                "horizon": HORIZON_DAYS,
                "y_pred": float(p > 0.5),
                "p_up": float(p),
                "source": source,
                "pipeline_run_id": pipeline_run_id,
            }
            for ts, p in zip(X.index, p_up, strict=True)
        ]
        async with self.sessionmaker() as s, s.begin():
            await prediction_repo.upsert_predictions(s, rows)
        # New forecast → cached /forecast and /status responses are now stale.
        await invalidate_namespaces("forecast", "status")
        return {
            "status": "success",
            "rows": len(rows),
            "model_version_id": mv.id,
            "model": f"{mv.name} v{mv.version}",
            "as_of": X.index[-1].isoformat(),
            "p_up": float(p_up[-1]),
            "source": source,
        }

    async def evaluate_outcomes(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        async with self.sessionmaker() as s:
            pending = await prediction_repo.pending_outcomes(s, now)
        rows = []
        for r in pending:
            ret = math.log(r.close_target / r.close_asof)
            up = ret > 0
            p = None if r.p_up is None else float(np.clip(r.p_up, EPS, 1 - EPS))
            rows.append(
                {
                    "prediction_id": r.id,
                    "realized_return": ret,
                    "realized_up": up,
                    "log_loss": None if p is None else -math.log(p if up else 1 - p),
                    "brier": None if p is None else (p - float(up)) ** 2,
                    "correct": bool((r.y_pred > 0.5) == up),
                }
            )
        async with self.sessionmaker() as s, s.begin():
            await prediction_repo.upsert_outcomes(s, rows)
        if rows:  # the 90-day track record shown with the forecast changed
            await invalidate_namespaces("forecast")
        return {"status": "success", "rows": len(rows)}
