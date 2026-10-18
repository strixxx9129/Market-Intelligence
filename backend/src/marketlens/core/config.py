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
