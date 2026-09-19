"""Liveness and readiness probes."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import SessionDep, SettingsDep
from app.core.logging import get_logger

router = APIRouter(tags=["health"])
log = get_logger(__name__)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    environment: str
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    database: Literal["ok", "unavailable"]


@router.get("/health", response_model=HealthResponse)
async def health(settings: SettingsDep) -> HealthResponse:
    """Liveness: the process is up. Does not touch the database."""
    return HealthResponse(status="ok", environment=settings.environment, version="0.1.0")


@router.get("/ready", response_model=ReadinessResponse)
async def ready(
    response: Response,
    session: SessionDep,
) -> ReadinessResponse:
    """Readiness: dependencies are reachable."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        log.warning("readiness_check_failed", dependency="database")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="degraded", database="unavailable")
    return ReadinessResponse(status="ready", database="ok")
