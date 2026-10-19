from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Freshness(BaseModel):
    symbol: str
    latest_candle_ts: datetime | None
    latest_feature_ts: datetime | None
    latest_forecast_as_of: datetime | None
    stale_days: int | None


class JobRun(BaseModel):
    job: str
    status: str
    attempt: int
    started_at: datetime
    finished_at: datetime | None
    error: str | None


class SystemAlert(BaseModel):
    severity: Literal["info", "warning", "critical"]
    code: str
    message: str


class StatusOut(BaseModel):
    status: Literal["ok", "degraded"]
    generated_at: datetime
    freshness: list[Freshness]
    last_runs: list[JobRun]
    production_model: str | None
    alerts: list[SystemAlert]
