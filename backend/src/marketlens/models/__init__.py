"""ORM models. Import every model here so `Base.metadata` is complete for Alembic."""

from marketlens.models.asset import Asset
from marketlens.models.backtest import Backtest
from marketlens.models.data_quality_issue import DataQualityIssue, IssueSeverity
from marketlens.models.feature import Feature
from marketlens.models.feature_set import FeatureSet
from marketlens.models.market_price import MarketPrice
from marketlens.models.pipeline_run import PipelineRun, PipelineRunStatus
from marketlens.models.prediction import Prediction, PredictionOutcome, PromotionDecision
from marketlens.models.raw_ingestion import RawIngestion
from marketlens.models.training import ModelVersion, OosPrediction, TrainingRun
from marketlens.models.user import (
    ALERT_CONDITIONS,
    Alert,
    ApiKey,
    RefreshToken,
    Role,
    User,
    Watchlist,
    WatchlistItem,
)

__all__ = [
    "ALERT_CONDITIONS",
    "Alert",
    "ApiKey",
    "Asset",
    "Backtest",
    "DataQualityIssue",
    "Feature",
    "FeatureSet",
    "IssueSeverity",
    "MarketPrice",
    "ModelVersion",
    "OosPrediction",
    "PipelineRun",
    "PipelineRunStatus",
    "Prediction",
    "PredictionOutcome",
    "PromotionDecision",
    "RawIngestion",
    "RefreshToken",
    "Role",
    "TrainingRun",
    "User",
    "Watchlist",
    "WatchlistItem",
]
