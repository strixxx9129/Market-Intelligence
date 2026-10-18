"""A stored backtest: a hypothetical strategy built from one model version's OUT-OF-SAMPLE
predictions, judged after costs against buy-and-hold and the baseline strategy.

`metrics` holds the full result (evaluation and selection periods, gross and net, benchmarks,
per era, sensitivity); the daily equity curve lives in object storage as Parquet
(`equity_curve_uri`, e.g. s3://backtests/<id>.parquet). Educational, not financial advice.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class Backtest(Base):
    __tablename__ = "backtests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    model_version_id: Mapped[int] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    baseline_model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    equity_curve_uri: Mapped[str | None] = mapped_column(String(512))
    git_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
