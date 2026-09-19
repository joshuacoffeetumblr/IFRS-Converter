from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(environment="ci", debug=True)


@pytest.fixture()
def app(settings: Settings) -> Iterator[FastAPI]:
    get_settings.cache_clear()
    application = create_app(settings)
    yield application
    get_settings.cache_clear()


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
