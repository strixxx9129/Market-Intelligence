"""SQL for `feature_sets` and `features`."""

from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from marketlens.models import Feature, FeatureSet

CHUNK_SIZE = 5_000


async def get_feature_set(session: AsyncSession, version: str) -> FeatureSet | None:
    return await session.scalar(select(FeatureSet).where(FeatureSet.version == version))


async def create_feature_set(
    session: AsyncSession, version: str, definition: dict[str, Any], code_hash: str
) -> FeatureSet:
    fs = FeatureSet(version=version, definition=definition, code_hash=code_hash)
    session.add(fs)
    await session.flush()
    return fs


async def upsert_features(
    session: AsyncSession, asset_id: int, feature_set_id: int, values: pd.DataFrame
) -> int:
    """UPSERT one row per index entry; returns rows inserted or actually changed."""
    base = insert(Feature)
    stmt = base.on_conflict_do_update(
        constraint="uq_features_asset_id_ts_feature_set_id",
        set_={"values": base.excluded["values"], "computed_at": func.now()},
        where=Feature.values.is_distinct_from(base.excluded["values"]),
    ).returning(Feature.id)

    rows = [
        {
            "asset_id": asset_id,
            "ts": ts.to_pydatetime(),
            "feature_set_id": feature_set_id,
            "values": {k: float(v) for k, v in record.items()},
        }
        for ts, record in zip(values.index, values.to_dict(orient="records"), strict=True)
    ]
    changed = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        result = await session.execute(stmt, rows[i : i + CHUNK_SIZE])
        changed += len(result.all())
    return changed


async def load_features(session: AsyncSession, asset_id: int, feature_set_id: int) -> pd.DataFrame:
    rows = (
        await session.execute(
            select(Feature.ts, Feature.values)
            .where(Feature.asset_id == asset_id, Feature.feature_set_id == feature_set_id)
            .order_by(Feature.ts)
        )
    ).all()
    index: list[datetime] = [r.ts for r in rows]
    return pd.DataFrame([r.values for r in rows], index=pd.DatetimeIndex(index, name="ts"))
