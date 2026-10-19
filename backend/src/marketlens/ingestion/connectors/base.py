"""Connector plugin interface and registry.

A connector knows how to read ONE data source and turn it into canonical candles
(`marketlens.ingestion.types.CANDLE_COLUMNS`). Everything after that (validation, UPSERT,
run bookkeeping, idempotency) is shared by the pipeline, so adding a new source means writing
one small class, not a new pipeline.

Two ways to register a connector:

1. In this repo: decorate the class with `@register_connector`.
2. From another package (no change to MarketLens needed): declare an entry point
       [project.entry-points."marketlens.connectors"]
       my_source = "my_pkg.connector:MyConnector"
   `load_plugins()` discovers every installed entry point in that group.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from importlib.metadata import entry_points
from typing import Any, ClassVar

from marketlens.core.logging import get_logger
from marketlens.ingestion.types import FetchResult, SourceFingerprint

ENTRY_POINT_GROUP = "marketlens.connectors"
logger = get_logger(__name__)


class BaseConnector(ABC):
    #: Unique registry key, used on the CLI: `marketlens ingest --source <name>`.
    name: ClassVar[str]
    #: Intervals this connector can produce, e.g. ("1d",).
    supported_intervals: ClassVar[tuple[str, ...]]
    #: Asset symbols this source covers; empty means "any symbol".
    supported_assets: ClassVar[tuple[str, ...]] = ()

    def refresh(self) -> dict[str, Any] | None:
        """Fetch fresh raw data from the remote source, if the connector has one.

        Called by the scheduled ingest task BEFORE `fetch()`; never called by the API.
        Returns a small summary for the run log, or None when there is nothing to refresh.
        Raise `TransientError` for failures worth retrying (network).
        """
        return None

    def fingerprint(self) -> SourceFingerprint | None:
        """Hash of the raw input, used to skip inputs that were already ingested.

        File-based sources return one; API sources return None and rely on UPSERT alone.
        """
        return None

    @abstractmethod
    def fetch(
        self, asset: str, start: datetime | None, end: datetime | None, interval: str = "1d"
    ) -> FetchResult:
        """Return complete candles with start <= ts < end (None = unbounded).

        Must never return an unfinished period (no look-ahead), and must report every raw
        record it could not use as a QualityIssue instead of dropping it silently.
        """

    def check_supports(self, asset: str, interval: str) -> None:
        if interval not in self.supported_intervals:
            raise ValueError(f"{self.name} does not support interval {interval!r}")
        if self.supported_assets and asset not in self.supported_assets:
            raise ValueError(f"{self.name} does not provide asset {asset!r}")


_REGISTRY: dict[str, type[BaseConnector]] = {}
_plugins_loaded = False


def register_connector[C: type[BaseConnector]](cls: C) -> C:
    existing = _REGISTRY.get(cls.name)
    if existing is not None and existing is not cls:
        raise ValueError(f"connector name {cls.name!r} already registered by {existing!r}")
    _REGISTRY[cls.name] = cls
    return cls


def load_plugins() -> None:
    """Import every connector advertised via the `marketlens.connectors` entry-point group."""
    global _plugins_loaded
    if _plugins_loaded:
        return
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            cls = ep.load()
        except Exception as exc:  # a broken third-party plugin must not break the others
            logger.warning("connector_plugin_failed", entry_point=ep.name, error=repr(exc))
            continue
        if not (isinstance(cls, type) and issubclass(cls, BaseConnector)):
            logger.warning("connector_plugin_invalid", entry_point=ep.name)
            continue
        register_connector(cls)
    _plugins_loaded = True


def available_connectors() -> list[str]:
    load_plugins()
    return sorted(_REGISTRY)


def get_connector(name: str, **kwargs: Any) -> BaseConnector:
    load_plugins()
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"unknown connector {name!r}; available: {', '.join(sorted(_REGISTRY)) or 'none'}"
        ) from None
    return cls(**kwargs)
