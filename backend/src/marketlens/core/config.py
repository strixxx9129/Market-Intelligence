"""Application settings, loaded from environment variables (12-factor config).

The same code/image runs in dev, CI and prod; only the environment differs.
Never hard-code URLs or secrets here: add a field, then set it in `.env` / the deployment env.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # staging and prod are both "deployed": same strict checks, different data and URLs.
    environment: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+asyncpg://marketlens:marketlens@localhost:5432/marketlens"
    test_database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # True when DATABASE_URL goes through PgBouncer in TRANSACTION mode (managed Postgres
    # poolers): consecutive statements may hit different server connections, so asyncpg's
    # cached prepared statements would vanish ("prepared statement ... does not exist").
    db_pgbouncer: bool = False

    redis_url: str = "redis://localhost:6379/0"

    # Where raw input files live (read-only for the pipeline). Compose mounts ../data at /data.
    raw_data_dir: Path = Path("data/raw")
    # Where generated reports are written (compose mounts ../docs at /docs).
    reports_dir: Path = Path("../docs")

    # MLflow tracking server; None = MLflow default (./mlruns). Compose sets http://mlflow:5000.
    mlflow_tracking_uri: str | None = None
    # Commit being run, injected by the Makefile; recorded with every training run.
    git_sha: str = "unknown"

    # S3-compatible object storage (SeaweedFS locally; any S3 in production).
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"
    backtest_bucket: str = "backtests"

    # --- Production pipeline (Celery) ---
    asset_symbol: str = "BTC-USD"
    feature_set: str = "v1"
    # Worker downloads the Kaggle dataset before ingesting (needs ~/.kaggle mounted).
    kaggle_refresh: bool = False
    # Promotion gate: candidate must beat the best baseline by this much mean fold log loss ...
    promotion_min_logloss_improvement: float = 0.001
    # ... and may be worse than it in at most this many folds.
    promotion_max_worse_folds: int = 8
    # validate: newest candle older than this many days is a (non-blocking) staleness warning.
    health_max_staleness_days: int = 3
    health_gap_lookback_days: int = 30
    model_cache_dir: Path = Path("/tmp/marketlens-models")
    lock_prefix: str = "marketlens:lock"

    # --- Public API (Phase 8) ---
    # HS256 signing key for access tokens. MUST be overridden in every real deployment.
    jwt_secret: str = "dev-only-change-me-32-bytes-minimum!!"
    jwt_issuer: str = "marketlens"
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600
    # Rate limits (slowapi syntax). Shared across API replicas through Redis.
    rate_limit_anonymous: str = "60/minute"
    rate_limit_user: str = "300/minute"
    rate_limit_api_key: str = "1000/minute"
    rate_limit_auth: str = "5/minute"
    rate_limit_enabled: bool = True
    cache_enabled: bool = True

    # --- Monitoring (Phase 9) ---
    freshness_max_hours: float = 36.0  # since the newest candle CLOSED (ts + 24 h)
    psi_warning: float = 0.10
    psi_alert: float = 0.25
    ks_alpha: float = 0.01  # KS p-value below this = "different" (second opinion only)
    drift_window_days: int = 30
    performance_min_n: int = 20  # fewer scored forecasts = "insufficient data", never an alert
    performance_logloss_margin: float = 0.01  # worse than the reference by more than this
    monitoring_alert_cooldown_hours: float = 24.0
    monitoring_webhook_url: str | None = None  # Discord or Slack incoming webhook
    sentry_dsn: str | None = None
    sentry_traces_sample_rate: float = 0.0

    # Comma-separated in the environment: CORS_ORIGINS=http://a,http://b
    cors_origins: Annotated[list[str], NoDecode] = []

    readiness_timeout_s: float = 2.0

    # --- Deployment (Phase 11) ---
    # Header the platform's edge proxy SETS (overwriting anything the client sent) with the
    # real client IP, e.g. "Fly-Client-IP". Only set it when the app is reachable exclusively
    # through that proxy; otherwise clients could forge it. None = use the socket peer address.
    trusted_client_ip_header: str | None = None
    # Interactive docs (/docs, /redoc, /openapi.json). Public by default: it's an open API.
    expose_docs: bool = True
    # HSTS max-age sent on HTTPS responses (1 year). 0 disables the header.
    hsts_max_age_s: int = 31_536_000

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "prod")

    @model_validator(mode="after")
    def _fail_fast_when_deployed(self) -> "Settings":
        """Refuse to start a deployed process with unsafe or missing config, listing EVERY
        problem at once (one redeploy to fix them all, not one per missing secret)."""
        if self.is_deployed:
            problems = deployment_problems(self)
            if problems:
                raise ValueError(
                    f"refusing to start with ENVIRONMENT={self.environment}:\n- "
                    + "\n- ".join(problems)
                )
        return self


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def deployment_problems(s: Settings) -> list[str]:
    """Everything that is fine on a laptop but wrong on the internet."""
    problems = []
    if s.jwt_secret.startswith("dev-only") or len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET must be a random secret of at least 32 characters")
    if _host(s.database_url) in LOCAL_HOSTS or "marketlens:marketlens@" in s.database_url:
        problems.append("DATABASE_URL points at localhost or uses the dev password")
    if _host(s.redis_url) in LOCAL_HOSTS:
        problems.append("REDIS_URL points at localhost")
    if not s.cors_origins:
        problems.append("CORS_ORIGINS is empty (set the website's https:// origin)")
    for origin in s.cors_origins:
        if origin == "*" or not origin.startswith("https://"):
            problems.append(f"CORS origin {origin!r} must be an explicit https:// origin")
    if not (s.s3_endpoint_url and s.s3_access_key and s.s3_secret_key):
        problems.append("S3_ENDPOINT_URL, S3_ACCESS_KEY and S3_SECRET_KEY are required")
    if not s.mlflow_tracking_uri:
        problems.append("MLFLOW_TRACKING_URI is required (rule 3: every training run is logged)")
    if s.git_sha in ("", "unknown"):
        problems.append("GIT_SHA must be set to the deployed commit (reproducibility)")
    if s.log_level.upper() == "DEBUG":
        problems.append("LOG_LEVEL=DEBUG may log sensitive request data")
    return problems


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
