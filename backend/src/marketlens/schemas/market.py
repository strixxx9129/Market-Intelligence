from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from marketlens.schemas.common import DISCLAIMER, PageInfo


class AssetOut(BaseModel):
    symbol: str
    name: str
    asset_class: str
    base_currency: str
    quote_currency: str
    latest_ts: datetime | None = Field(
        None, description="Open time (UTC) of the newest daily candle"
    )
    latest_close: float | None = None
    prev_close: float | None = Field(None, description="Close of the previous daily candle")
    change_1d_pct: float | None = Field(
        None, description="Close-to-close change vs the previous daily candle, in percent"
    )


class AssetList(BaseModel):
    data: list[AssetOut]


class Candle(BaseModel):
    ts: datetime = Field(description="Candle open time, UTC")
    open: float
    high: float
    low: float
    close: float
    volume: float


class PricePage(BaseModel):
    symbol: str
    interval: str
    data: list[Candle]
    page: PageInfo


class FeatureRow(BaseModel):
    ts: datetime = Field(description="Features use candles up to and including this day")
    values: dict[str, float]


class FeaturesOut(BaseModel):
    symbol: str
    feature_set: str
    feature_set_code_hash: str
    features: list[dict[str, Any]] = Field(description="Name, family, lookback, description")
    data: list[FeatureRow]


class TrackRecord(BaseModel):
    window_days: int
    n: int = Field(description="Live forecasts in the window whose outcome is known")
    accuracy: float | None = None
    log_loss: float | None = None
    brier: float | None = None
    always_up_accuracy: float | None = Field(
        None, description="Share of up days on the same dates: what 'always up' would score"
    )
    coin_flip_log_loss: float = Field(0.6931, description="ln 2: log loss of always saying 50%")
    note: str


class ForecastOut(BaseModel):
    symbol: str
    model_version: str = Field(description="name v<version> of the model that made this forecast")
    model_version_id: int
    model_stage: str
    is_baseline_model: bool
    as_of_ts: datetime = Field(description="Last candle the forecast used (UTC day open)")
    target_ts: datetime = Field(description="The day being forecast (UTC day open)")
    horizon_days: int
    p_up: float | None
    y_pred: float
    created_at: datetime
    track_record: TrackRecord
    disclaimer: str = DISCLAIMER
