"""Aggregates every /api/v1 router. New feature routers are included here."""

from fastapi import APIRouter, Depends

from marketlens.api import health
from marketlens.api.ratelimit import enforce_rate_limit
from marketlens.api.v1 import admin, auth, me, public

# Every v1 route goes through the rate limiter (health probes are exempted in main.py).
api_router = APIRouter(dependencies=[Depends(enforce_rate_limit)])
api_router.include_router(health.router)
api_router.include_router(public.router)
api_router.include_router(auth.router)
api_router.include_router(me.router)
api_router.include_router(admin.router)
