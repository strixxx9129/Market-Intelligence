"""A versioned, immutable feature-set definition.

`version` (e.g. "v1") names a frozen list of features. `code_hash` is a SHA-256 over the
definition and the source code of every feature function in it. If someone edits a feature
and rebuilds the same version, the hash no longer matches and the builder refuses: changed
code means a new version (rule 3, reproducibility). A model trained on "v1" can therefore
always be traced back to exactly the code that produced its inputs.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base


class FeatureSet(Base):
    __tablename__ = "feature_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
