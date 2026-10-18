import pytest

from marketlens.core.config import Settings


def test_cors_origins_parsed_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test,")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("REDIS_URL", "redis://cache:6379/1")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.environment == "prod"
    assert settings.redis_url == "redis://cache:6379/1"


def test_invalid_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging-ish")

    with pytest.raises(ValueError):
        Settings(_env_file=None)  # type: ignore[call-arg]
