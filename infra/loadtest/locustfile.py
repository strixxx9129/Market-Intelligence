"""Locust load test for the public read API: forecast and prices (with cursor pagination).

Run (headless, prints p50/p95/RPS):   make loadtest
UI:                                    make loadtest-ui   →  http://localhost:8089

Environment:
  ML_API_KEY   optional API key (higher rate limit). Without one, every simulated user shares
               the anonymous per-IP limit and you'll mostly measure 429s, which is the limiter
               working, not a throughput number. `make loadtest` runs against a separate API
               instance with rate limiting disabled to measure the service itself.
"""

import os
import random

from locust import HttpUser, between, task

SYMBOL = os.environ.get("ML_SYMBOL", "BTC-USD")
API_KEY = os.environ.get("ML_API_KEY")


class ApiUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        if API_KEY:
            self.client.headers["X-API-Key"] = API_KEY

    @task(5)
    def forecast(self) -> None:
        self.client.get(f"/api/v1/assets/{SYMBOL}/forecast", name="forecast")

    @task(3)
    def latest_prices(self) -> None:
        self.client.get(
            f"/api/v1/assets/{SYMBOL}/prices",
            params={"from": "2026-01-01T00:00:00Z", "limit": 100},
            name="prices (recent)",
        )

    @task(1)
    def paginate_prices(self) -> None:
        """Follow next_cursor a few pages, like a chart loading history."""
        cursor = None
        for _ in range(random.randint(2, 4)):
            params: dict[str, object] = {"limit": 500}
            if cursor:
                params["cursor"] = cursor
            with self.client.get(
                f"/api/v1/assets/{SYMBOL}/prices", params=params, name="prices (paginate)",
                catch_response=True,
            ) as response:
                if response.status_code != 200:
                    response.failure(f"status {response.status_code}")
                    return
                cursor = response.json()["page"]["next_cursor"]
            if cursor is None:
                return

    @task(1)
    def status(self) -> None:
        self.client.get("/api/v1/status", name="status")
