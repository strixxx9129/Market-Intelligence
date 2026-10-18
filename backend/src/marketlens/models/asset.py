from sqlalchemy import Boolean, String, true
from sqlalchemy.orm import Mapped, mapped_column

from marketlens.db.base import Base, TimestampMixin


class Asset(TimestampMixin, Base):
    """A tradable instrument, e.g. BTC-USD. `symbol` is the natural key."""

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(32), nullable=False)  # crypto, equity, ...
    base_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(16), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=true(), nullable=False)
