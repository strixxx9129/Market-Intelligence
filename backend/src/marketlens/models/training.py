"""Training bookkeeping: training_runs, model_versions, oos_predictions.

- training_runs: one row per `marketlens train` invocation. Holds everything needed to
  reproduce it (rule 3): split config, dataset hash, feature set, git SHA, seed, plus the
  per-model metric summary and the MLflow parent run id.
- model_versions: one IMMUTABLE row per trained model. A DB trigger (migration 0004) rejects
  any UPDATE except to `stage`, so a version can never silently change after the fact.
- oos_predictions: out-of-sample predictions per fold. These are the only honest inputs for a
  backtest, because every prediction was made by a model that never saw that day.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base
from marketlens.models.pipeline_run import PipelineRunStatus

MODEL_STAGES = ("candidate", "staging", "production", "archived")


class TrainingRun(Base):
    __tablename__ = "training_runs"
    __table_args__ = (
        CheckConstraint("task IN ('regression', 'classification')", name="task_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_set_id: Mapped[int] = mapped_column(
        ForeignKey("feature_sets.id", ondelete="RESTRICT"), nullable=False
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    split_config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    dataset_hash: Mapped[str | None] = mapped_column(String(64))
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PipelineRunStatus] = mapped_column(
        Enum(
            PipelineRunStatus,
            name="training_run_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PipelineRunStatus.RUNNING,
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    mlflow_experiment: Mapped[str | None] = mapped_column(String(128))
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint(
            "stage IN (" + ", ".join(f"'{s}'" for s in MODEL_STAGES) + ")", name="stage_valid"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. btc-usd_1d_logistic
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    algo: Mapped[str] = mapped_column(String(64), nullable=False)
    task: Mapped[str] = mapped_column(String(16), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    feature_set_id: Mapped[int] = mapped_column(
        ForeignKey("feature_sets.id", ondelete="RESTRICT"), nullable=False
    )
    training_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_runs.id", ondelete="RESTRICT"), nullable=False
    )
    train_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    train_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    artifact_uri: Mapped[str | None] = mapped_column(String(512))
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OosPrediction(Base):
    __tablename__ = "oos_predictions"
    __table_args__ = (UniqueConstraint("model_version_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    training_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("training_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    fold: Mapped[int] = mapped_column(Integer, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    y_true: Mapped[float] = mapped_column(Float, nullable=False)
    y_pred: Mapped[float] = mapped_column(Float, nullable=False)
    proba: Mapped[float | None] = mapped_column(Float)  # P(up), classification only
