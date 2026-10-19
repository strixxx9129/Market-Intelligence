"""Public market data: assets, prices (cursor-paginated), features, forecasts.

Everything here only READS what the pipeline already computed (rule 5). Forecasts always come
with the model version that made them, their timestamps and a disclaimer (rule 6).

Cursor vs offset pagination. Offset (`?page=40`) makes the database skip 39 pages of rows on
every request, gets slower the deeper you go, and silently shifts if new rows are inserted
while a client pages. A cursor ("give me rows after ts X") turns into
`WHERE ts > X ORDER BY ts LIMIT n` on the unique index: constant cost per page, and stable
under inserts. The cursor is opaque to clients (base64), so its format can change later.
"""

import base64
import binascii
import math
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.core.errors import ServiceError
from marketlens.models import Asset
from marketlens.repositories import market_read_repo as repo
from marketlens.repositories import prediction_repo
from marketlens.schemas.common import PageInfo
from marketlens.schemas.market import (
    AssetList,
    AssetOut,
    Candle,
    FeatureRow,
    FeaturesOut,
    ForecastOut,
    PricePage,
    TrackRecord,
)

TRACK_RECORD_DAYS = 90
MIN_TRACK_RECORD = 30


def encode_cursor(ts: datetime) -> str:
    return base64.urlsafe_b64encode(ts.isoformat().encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> datetime:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        ts = datetime.fromisoformat(base64.urlsafe_b64decode(padded).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise ServiceError(400, "invalid cursor", code="invalid_cursor") from exc
    if ts.tzinfo is None:
        raise ServiceError(400, "invalid cursor", code="invalid_cursor")
    return ts


async def _asset(session: AsyncSession, symbol: str) -> Asset:
    asset = await repo.get_asset(session, symbol)
    if asset is None:
        raise ServiceError(404, f"asset {symbol!r} not found", code="asset_not_found")
    return asset


async def list_assets(session: AsyncSession) -> AssetList:
    candles: dict[str, list[Any]] = {}
    assets: dict[str, Asset] = {}
    for r in await repo.list_assets(session):
        assets[r.Asset.symbol] = r.Asset
        if r.ts is not None:
            candles.setdefault(r.Asset.symbol, []).append(r)  # newest first
    out = []
    for symbol, a in assets.items():
        rows = candles.get(symbol, [])
        latest = rows[0] if rows else None
        prev = rows[1] if len(rows) > 1 else None
        change = (
            latest.close / prev.close - 1
            if latest is not None and prev is not None and prev.close
            else None
        )
        out.append(
            AssetOut(
                symbol=a.symbol,
                name=a.name,
                asset_class=a.asset_class,
                base_currency=a.base_currency,
                quote_currency=a.quote_currency,
                latest_ts=latest.ts if latest else None,
                latest_close=latest.close if latest else None,
                prev_close=prev.close if prev else None,
                change_1d_pct=round(change * 100, 4) if change is not None else None,
            )
        )
    return AssetList(data=out)


async def prices(
    session: AsyncSession,
    symbol: str,
    interval: str,
    start: datetime | None,
    end: datetime | None,
    cursor: str | None,
    limit: int,
) -> PricePage:
    asset = await _asset(session, symbol)
    after = decode_cursor(cursor) if cursor else None
    rows = await repo.price_page(session, asset.id, interval, after, start, end, limit)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return PricePage(
        symbol=asset.symbol,
        interval=interval,
        data=[
            Candle(ts=r.ts, open=r.open, high=r.high, low=r.low, close=r.close, volume=r.volume)
            for r in rows
        ],
        page=PageInfo(limit=limit, next_cursor=encode_cursor(rows[-1].ts) if has_more else None),
    )


async def features(session: AsyncSession, symbol: str, version: str, limit: int) -> FeaturesOut:
    asset = await _asset(session, symbol)
    production = await prediction_repo.production_model(session, asset.symbol)
    fs = (
        await repo.get_feature_set(session, production.feature_set_id)
        if production is not None
        else await repo.feature_set_by_version(session, version)
    )
    if fs is None:
        raise ServiceError(404, "no feature set built yet", code="features_not_found")
    rows = await repo.latest_features(session, asset.id, fs.id, limit)
    return FeaturesOut(
        symbol=asset.symbol,
        feature_set=fs.version,
        feature_set_code_hash=fs.code_hash,
        features=fs.definition["features"],
        data=[FeatureRow(ts=r.ts, values=r.values) for r in rows],
    )


async def forecast(session: AsyncSession, symbol: str, now: datetime | None = None) -> ForecastOut:
    asset = await _asset(session, symbol)
    mv = await prediction_repo.production_model(session, asset.symbol)
    if mv is None:
        raise ServiceError(404, "no production model yet", code="forecast_not_found")
    pred = await repo.latest_live_prediction(session, asset.id, mv.id)
    if pred is None:
        raise ServiceError(
            404, "no forecast from the production model yet", code="forecast_not_found"
        )
    run = await repo.get_training_run(session, mv.training_run_id)
    is_baseline = bool(run and run.metrics.get("models", {}).get(mv.algo, {}).get("is_baseline"))
    return ForecastOut(
        symbol=asset.symbol,
        model_version=f"{mv.name} v{mv.version}",
        model_version_id=mv.id,
        model_stage=mv.stage,
        is_baseline_model=is_baseline,
        as_of_ts=pred.as_of_ts,
        target_ts=pred.target_ts,
        horizon_days=pred.horizon,
        p_up=pred.p_up,
        y_pred=pred.y_pred,
        created_at=pred.created_at,
        track_record=await track_record(session, asset.id, now),
    )


async def track_record(
    session: AsyncSession, asset_id: int, now: datetime | None = None
) -> TrackRecord:
    """Honest live record: only `live` forecasts (made before their target day), never
    backfills, compared on the SAME days with 'always up' and a coin flip."""
    now = now or datetime.now(UTC)
    rows = await repo.scored_live_predictions(
        session, asset_id, now - timedelta(days=TRACK_RECORD_DAYS)
    )
    n = len(rows)
    if n == 0:
        return TrackRecord(
            window_days=TRACK_RECORD_DAYS,
            n=0,
            note="No scored live forecasts yet: live forecasting has only just started.",
        )
    ll = [r.log_loss for r in rows if r.log_loss is not None]
    br = [r.brier for r in rows if r.brier is not None]
    return TrackRecord(
        window_days=TRACK_RECORD_DAYS,
        n=n,
        accuracy=sum(r.correct for r in rows) / n,
        log_loss=sum(ll) / len(ll) if ll else None,
        brier=sum(br) / len(br) if br else None,
        always_up_accuracy=sum(r.realized_up for r in rows) / n,
        coin_flip_log_loss=round(math.log(2), 4),
        note=(
            f"Only {n} scored forecasts: far too few to judge skill."
            if n < MIN_TRACK_RECORD
            else "Compare accuracy with always_up_accuracy and log_loss with coin_flip_log_loss."
        ),
    )
