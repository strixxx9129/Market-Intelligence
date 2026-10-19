from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from marketlens.schemas.common import DISCLAIMER


class MetricStat(BaseModel):
    mean: float | None
    std: float | None


class LeaderboardEntry(BaseModel):
    model_version_id: int | None
    name: str
    version: int | None
    family: str
    is_baseline: bool
    stage: str | None
    primary_metric: str
    score: MetricStat
    vs_best_baseline: dict[str, Any] | None = None
    per_fold: list[float] | None = Field(
        None, description="Primary-metric score on each walk-forward test fold, in time order"
    )


class Leaderboard(BaseModel):
    task: str
    training_run_id: str
    finished_at: datetime | None
    folds: int
    primary_metric: str
    verdict: dict[str, Any]
    entries: list[LeaderboardEntry]


class LeaderboardList(BaseModel):
    data: list[Leaderboard]
    disclaimer: str = DISCLAIMER


class PromotionDecisionOut(BaseModel):
    decision: str
    reasons: list[str]
    decided_at: datetime
    candidate_model_version_id: int | None
    production_model_version_id: int | None


class ModelCard(BaseModel):
    id: int
    name: str
    version: int
    algo: str
    task: str
    stage: str
    params: dict[str, Any]
    created_at: datetime
    train_start: datetime
    train_end: datetime
    feature_set: str
    feature_set_code_hash: str
    training: dict[str, Any] = Field(description="Split config, dataset hash, git SHA, seed")
    metrics: dict[str, Any] = Field(description="Walk-forward out-of-sample metrics")
    promotion_history: list[PromotionDecisionOut]
    backtests: list[dict[str, Any]]
    mlflow_run_id: str | None
    disclaimer: str = DISCLAIMER


class EquityCurve(BaseModel):
    """Weekly growth of 1 unit invested (hypothetical), evaluation period only."""

    frequency: str
    ts: list[datetime]
    strategy_gross: list[float | None] = Field(description="Before costs")
    strategy_net: list[float | None] = Field(description="After fees and slippage")
    buy_and_hold: list[float | None]
    baseline: list[float | None] = Field(description="Same rules driven by the baseline model")


class BacktestSummary(BaseModel):
    id: str
    model_version_id: int
    model_name: str
    model_version: int
    created_at: datetime
    evaluation_start: str | None
    evaluation_end: str | None
    cost_bps: float
    strategy_net_total_return: float | None
    buy_and_hold_total_return: float | None
    strategy_net_sharpe: float | None
    buy_and_hold_sharpe: float | None
    beats_buy_and_hold_sharpe: bool


class BacktestList(BaseModel):
    data: list[BacktestSummary]
    disclaimer: str = DISCLAIMER


class BacktestOut(BaseModel):
    id: str
    model_version_id: int
    model: dict[str, Any]
    created_at: datetime
    config: dict[str, Any]
    threshold: dict[str, Any]
    evaluation: dict[str, Any]
    selection: dict[str, Any]
    yearly: dict[str, Any]
    sensitivity: dict[str, Any]
    verdict: dict[str, Any]
    equity_curve_uri: str | None
    equity_curve: EquityCurve | None = None
    disclaimer: str
