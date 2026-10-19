from pathlib import Path

import pytest

from marketlens.core.config import Settings, deployment_problems
from marketlens.ingestion.kaggle_download import kaggle_credentials_present

GOOD_DEPLOYED_ENV = {
    "JWT_SECRET": "x" * 48,
    "DATABASE_URL": "postgresql+asyncpg://app:s3cret@db.internal:5432/marketlens",
    "REDIS_URL": "redis://default:pw@cache.internal:6379/0",
    "CORS_ORIGINS": "https://marketlens.pages.dev",
    "S3_ENDPOINT_URL": "https://acct.r2.cloudflarestorage.com",
    "S3_ACCESS_KEY": "key",
    "S3_SECRET_KEY": "secret",
    "MLFLOW_TRACKING_URI": "http://marketlens-mlflow.flycast",
    "GIT_SHA": "abc1234",
}


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test,")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # Don't inherit the runner's ENVIRONMENT (CI sets "test"): assert the code default.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.environment == "dev"
    assert settings.redis_url == "redis://cache:6379/1"


def test_invalid_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging-ish")

    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_deployed_settings_with_dev_defaults_refuse_to_start_listing_every_problem(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    # A laptop's .env copied to a server: explicit, so the container's own env can't leak in.
    for key in ("JWT_SECRET", "CORS_ORIGINS", "S3_ENDPOINT_URL", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://marketlens:marketlens@localhost/m")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "")
    monkeypatch.setenv("GIT_SHA", "unknown")
    with pytest.raises(ValueError) as exc:
        Settings(_env_file=None)  # type: ignore[call-arg]
    message = str(exc.value)
    for expected in (
        "JWT_SECRET",
        "DATABASE_URL",
        "REDIS_URL",
        "CORS_ORIGINS",
        "S3_",
        "MLFLOW",
        "GIT_SHA",
    ):
        assert expected in message  # all at once, not one per redeploy


@pytest.mark.parametrize("environment", ["staging", "prod"])
def test_good_deployed_settings_start(monkeypatch: pytest.MonkeyPatch, environment: str) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    for key, value in GOOD_DEPLOYED_ENV.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.is_deployed and deployment_problems(settings) == []


@pytest.mark.parametrize(
    ("key", "value", "fragment"),
    [
        ("CORS_ORIGINS", "*", "explicit https://"),
        ("CORS_ORIGINS", "http://marketlens.pages.dev", "explicit https://"),
        ("JWT_SECRET", "short-secret", "JWT_SECRET"),
        ("DATABASE_URL", "postgresql+asyncpg://marketlens:marketlens@db:5432/x", "dev password"),
        ("LOG_LEVEL", "DEBUG", "DEBUG"),
    ],
)
def test_each_unsafe_value_is_reported(
    monkeypatch: pytest.MonkeyPatch, key: str, value: str, fragment: str
) -> None:
    for k, v in {**GOOD_DEPLOYED_ENV, key: value}.items():
        monkeypatch.setenv(k, v)
    problems = deployment_problems(Settings(_env_file=None))  # type: ignore[call-arg]
    assert len(problems) == 1 and fragment in problems[0]


def test_dev_settings_are_never_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert not settings.is_deployed  # dev defaults would fail every check, but dev is fine


def test_kaggle_credentials_detection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for key in ("KAGGLE_API_TOKEN", "KAGGLE_USERNAME", "KAGGLE_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("KAGGLE_CONFIG_DIR", str(tmp_path))
    assert not kaggle_credentials_present()
    monkeypatch.setenv("KAGGLE_USERNAME", "ada")
    assert not kaggle_credentials_present()  # username without key is not enough
    monkeypatch.setenv("KAGGLE_KEY", "k")
    assert kaggle_credentials_present()
    monkeypatch.delenv("KAGGLE_KEY")
    (tmp_path / "access_token").write_text("t")
    assert kaggle_credentials_present()
