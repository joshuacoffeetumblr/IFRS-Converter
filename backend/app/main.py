"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import health, meta
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, json_output=settings.environment != "local")
    log.info("application_startup", environment=settings.environment)
    yield
    await dispose_engine()
    log.info("application_shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="IFRS 18 Impact Analyzer API",
        version="0.1.0",
        description=(
            "Restructures a reported income statement under IFRS 18 and explains "
            "the resulting change in operating profit, with a full audit trail."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = settings

    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(meta.router, prefix=settings.api_prefix)

    return app


app = create_app()
