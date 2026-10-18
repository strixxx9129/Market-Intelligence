"""Ledger of raw input files that were ingested successfully.

UNIQUE(source, file_hash) is what makes re-running the pipeline on the same file a no-op:
the SHA-256 identifies the *content*, so a renamed copy is still recognised, and a re-downloaded
file with new rows is not. The row is written in the same transaction as the candles, so a
crashed run never marks its file as done.
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class RawIngestion(Base):
    __tablename__ = "raw_ingestions"
    __table_args__ = (UniqueConstraint("source", "file_hash"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    rows_read: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="RESTRICT"), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
