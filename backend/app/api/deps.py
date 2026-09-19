"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_session


def get_request_settings(request: Request) -> Settings:
    """Resolve settings from application state, not from the process-wide cache.

    Reading the cached singleton directly would make ``create_app(settings=...)``
    silently inert, so tests and a running server could disagree about
    configuration — including the accounting thresholds in
    ``Settings.classification``. Resolving through app state keeps the factory
    argument authoritative.
    """
    return request.app.state.settings  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_request_settings)]


SessionDep = Annotated[AsyncSession, Depends(get_session)]
