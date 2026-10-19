"""Public, read-only market endpoints. Cached (Redis + ETag) and rate limited.

Routers only translate HTTP ↔ services; no business logic or SQL here.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from marketlens.api.deps import CacheDep, Session, cached_response
from marketlens.schemas.common import ERROR_RESPONSES
from marketlens.schemas.market import AssetList, FeaturesOut, ForecastOut, PricePage
from marketlens.schemas.models import BacktestOut, LeaderboardList, ModelCard
from marketlens.schemas.status import StatusOut
from marketlens.services import market, model_registry, status

router = APIRouter(responses=ERROR_RESPONSES)


@router.get("/assets", response_model=AssetList, tags=["market"])
async def list_assets(request: Request, session: Session, cache: CacheDep) -> Response:
    """Tracked assets with their newest daily candle."""
    return await cached_response(request, cache, "assets", lambda: market.list_assets(session))


@router.get("/assets/{symbol}/prices", response_model=PricePage, tags=["market"])
async def prices(
    request: Request,
    session: Session,
    cache: CacheDep,
    symbol: str,
    interval: Annotated[str, Query(pattern="^1d$", description="Only daily candles so far")] = "1d",
    start: Annotated[datetime | None, Query(alias="from", description="Inclusive, UTC")] = None,
    end: Annotated[datetime | None, Query(alias="to", description="Inclusive, UTC")] = None,
    cursor: Annotated[
        str | None, Query(description="`page.next_cursor` of the previous page")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 365,
) -> Response:
    """Daily OHLCV, oldest first, cursor-paginated on the candle timestamp."""
    query = {"interval": interval, "from": start, "to": end, "cursor": cursor, "limit": limit}
    return await cached_response(
        request,
        cache,
        "prices",
        lambda: market.prices(session, symbol, interval, start, end, cursor, limit),
        query,
    )


@router.get("/assets/{symbol}/features", response_model=FeaturesOut, tags=["market"])
async def features(
    request: Request,
    session: Session,
    cache: CacheDep,
    symbol: str,
    limit: Annotated[int, Query(ge=1, le=365)] = 30,
) -> Response:
    """Latest feature rows of the production model's feature set (each row uses data <= ts)."""
    return await cached_response(
        request,
        cache,
        "features",
        lambda: market.features(session, symbol, "v1", limit),
        {"limit": limit},
    )


@router.get("/assets/{symbol}/forecast", response_model=ForecastOut, tags=["market"])
async def forecast(request: Request, session: Session, cache: CacheDep, symbol: str) -> Response:
    """Next-day forecast from the Production model, with its 90-day live track record.

    Educational output, not financial advice.
    """
    return await cached_response(
        request, cache, "forecast", lambda: market.forecast(session, symbol)
    )


@router.get("/models", response_model=LeaderboardList, tags=["models"])
async def leaderboard(request: Request, session: Session, cache: CacheDep) -> Response:
    """Walk-forward leaderboard of the latest training run per task, baselines included."""
    return await cached_response(
        request, cache, "models", lambda: model_registry.leaderboard(session)
    )


@router.get("/models/{model_version_id}", response_model=ModelCard, tags=["models"])
async def model_card(
    request: Request, session: Session, cache: CacheDep, model_version_id: int
) -> Response:
    """Model card: parameters, data lineage, out-of-sample metrics, promotion history."""
    return await cached_response(
        request, cache, "models", lambda: model_registry.model_card(session, model_version_id)
    )


@router.get("/backtests/{backtest_id}", response_model=BacktestOut, tags=["models"])
async def backtest(
    request: Request, session: Session, cache: CacheDep, backtest_id: str
) -> Response:
    """A stored backtest (hypothetical, after costs, vs buy-and-hold)."""
    return await cached_response(
        request, cache, "backtests", lambda: model_registry.backtest(session, backtest_id)
    )


@router.get("/status", response_model=StatusOut, tags=["status"])
async def platform_status(request: Request, session: Session, cache: CacheDep) -> Response:
    """Data freshness, last run of every scheduled job, and active system alerts."""
    return await cached_response(request, cache, "status", lambda: status.platform_status(session))
