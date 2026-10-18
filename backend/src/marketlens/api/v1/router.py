"""Aggregates every /api/v1 router. New feature routers are included here."""

from fastapi import APIRouter

from marketlens.api import health

api_router = APIRouter()
api_router.include_router(health.router)
