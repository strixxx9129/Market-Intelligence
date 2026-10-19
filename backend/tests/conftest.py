from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from marketlens.core.config import get_settings
from marketlens.main import create_app


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Everything under tests/integration/ gets the `integration` marker automatically."""
    for item in items:
        if "integration" in Path(str(item.fspath)).parts:
            item.add_marker(pytest.mark.integration)


@pytest.fixture(autouse=True)
def _no_local_raw_data(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Tests never see the developer's downloaded dataset (data/raw is ~300 MB and is not in
    CI). Without this, a test can pass locally only because the real file happens to exist,
    which is exactly how CI stayed red unnoticed in Phase 7. Use tests/fixtures instead."""
    settings = get_settings()
    original = settings.raw_data_dir
    settings.raw_data_dir = tmp_path_factory.mktemp("raw")
    try:
        yield
    finally:
        settings.raw_data_dir = original


@pytest.fixture
def app() -> FastAPI:
    return create_app()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """HTTP client calling the ASGI app in-process (no server, no lifespan)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
