from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

Severity = Literal["warning", "critical"]


@dataclass
class MetricResult:
    metric_name: str
    ts: datetime  # the as-of time this value describes
    value: float | None
    threshold: float | None
    breached: bool
    asset_id: int | None = None
    model_version_id: int | None = None
    severity: Severity = "warning"
    message: str = ""  # human-readable, used in alerts
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def alert_key(self) -> str:
        return f"{self.metric_name}:{self.asset_id}:{self.model_version_id}"
