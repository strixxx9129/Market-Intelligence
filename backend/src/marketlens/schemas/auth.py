import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from marketlens.models import ALERT_CONDITIONS, Role


def _normalise_email(value: str) -> str:
    """Deliberately simple: one @, a dot in the domain, no spaces. Real verification is a
    confirmation e-mail, which no regex can replace."""
    value = value.strip().lower()
    local, _, domain = value.partition("@")
    if not local or "." not in domain or " " in value or value.count("@") != 1:
        raise ValueError("not a valid e-mail address")
    return value


Email = Annotated[str, Field(min_length=3, max_length=320), AfterValidator(_normalise_email)]


class RegisterIn(BaseModel):
    email: Email
    password: str = Field(min_length=12, max_length=256)


class LoginIn(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=256)


class TokenPair(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds")
    refresh_token: str = Field(description="Single use: every refresh returns a new one")


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: Role
    created_at: datetime


# ---------------------------------------------------------------- /me
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    symbols: list[str] = Field(default_factory=list, max_length=200)


class WatchlistOut(BaseModel):
    id: int
    name: str
    symbols: list[str]
    created_at: datetime
    updated_at: datetime


AlertCondition = Literal["p_up_above", "p_up_below", "price_above", "price_below"]
assert set(AlertCondition.__args__) == set(ALERT_CONDITIONS)  # type: ignore[attr-defined]


class AlertIn(BaseModel):
    symbol: str
    condition: AlertCondition
    threshold: float
    is_active: bool = True


class AlertPatch(BaseModel):
    condition: AlertCondition | None = None
    threshold: float | None = None
    is_active: bool | None = None


class AlertOut(BaseModel):
    id: int
    symbol: str
    condition: str
    threshold: float
    is_active: bool
    last_triggered_at: datetime | None
    trigger_count: int
    created_at: datetime
    updated_at: datetime


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="Shown ONCE. Only a hash is stored; it can't be recovered.")


# ---------------------------------------------------------------- admin
class PromoteIn(BaseModel):
    force: bool = Field(False, description="Override a failing gate (requires a reason)")
    reason: str | None = Field(None, max_length=500)


class PromoteOut(BaseModel):
    decision: str
    reasons: list[str]
    production_model_version_id: int | None


class RetryOut(BaseModel):
    pipeline_run_id: uuid.UUID
    task: str
    params: dict[str, object]
    celery_task_id: str
