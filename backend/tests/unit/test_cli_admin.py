"""Incident CLI commands used by the runbooks."""

import pytest
from typer.testing import CliRunner

from marketlens import cli
from marketlens.core import cache

runner = CliRunner()


def test_flush_cache_all_or_some_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    flushed: list[tuple[str, ...]] = []

    async def fake(*namespaces: str) -> None:
        flushed.append(namespaces)

    monkeypatch.setattr(cache, "invalidate_namespaces", fake)
    assert runner.invoke(cli.app, ["admin", "flush-cache"]).exit_code == 0
    assert flushed[-1] == tuple(sorted(cache.TTL))
    result = runner.invoke(cli.app, ["admin", "flush-cache", "--namespace", "forecast"])
    assert result.exit_code == 0 and flushed[-1] == ("forecast",)


def test_flush_cache_rejects_unknown_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(*namespaces: str) -> None:
        raise AssertionError("must not flush anything")

    monkeypatch.setattr(cache, "invalidate_namespaces", fake)
    result = runner.invoke(cli.app, ["admin", "flush-cache", "--namespace", "forcast"])
    assert result.exit_code != 0 and "unknown namespace" in result.output


def test_forced_promotion_needs_a_reason() -> None:
    result = runner.invoke(cli.app, ["admin", "promote", "1", "--force"])
    assert result.exit_code != 0
