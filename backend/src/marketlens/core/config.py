"""Application settings, loaded from environment variables (12-factor config).

The same code/image runs in dev, CI and prod; only the environment differs.
Never hard-code URLs or secrets here: add a field, then set it in `.env` / the deployment env.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+asyncpg://marketlens:marketlens@localhost:5432/marketlens"
    test_database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 10

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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is parsed once per process."""
    return Settings()
