from fastapi import FastAPI
from httpx import AsyncClient


async def test_readyz_ok_with_real_postgres_and_redis(app: FastAPI, client: AsyncClient) -> None:
    # Run the real lifespan so the app creates its actual engine and Redis client.
    async with app.router.lifespan_context(app):
        response = await client.get("/readyz")

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}
