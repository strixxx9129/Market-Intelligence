"""Computed feature values: one row per (asset, day, feature set).

`values` is JSONB ({"log_return_1d": 0.012, ...}) so adding features in a new set needs no
schema migration. Feature values at `ts` use only candles with ts <= this ts (no look-ahead).
Targets (the future) are deliberately NOT stored here; see `marketlens.features.targets`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("asset_id", "ts", "feature_set_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_set_id: Mapped[int] = mapped_column(
        ForeignKey("feature_sets.id", ondelete="RESTRICT"), nullable=False
    )
    values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
