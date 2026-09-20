from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

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


@pytest.fixture()
def uploads_dir(tmp_path: Path, settings: Settings) -> Iterator[Path]:
    """Point file storage at a scratch directory instead of the working tree."""
    original = settings.upload.directory
    settings.upload.directory = tmp_path / "uploads"
    yield settings.upload.directory
    settings.upload.directory = original


@pytest.fixture()
def statement_bytes(tmp_path: Path) -> bytes:
    """A synthetic Korean income statement, as uploaded bytes."""
    from tests.fixtures.korean_income_statement import build_workbook

    return build_workbook(tmp_path / "source.xlsx").read_bytes()


@pytest.fixture()
def fact_statement_bytes(tmp_path: Path) -> bytes:
    """A statement whose lines the rules cannot decide without asking."""
    from tests.fixtures.korean_income_statement import build_fact_workbook

    return build_fact_workbook(tmp_path / "facts.xlsx").read_bytes()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A session whose transaction is rolled back after each test."""
    settings = Settings(environment="ci")
    engine = create_async_engine(str(settings.database_url), poolclass=NullPool)

    try:
        connection = await engine.connect()
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"PostgreSQL not reachable: {exc}")

    transaction = await connection.begin()
    # `create_savepoint` makes the session run inside a SAVEPOINT nested in the
    # outer transaction. Without it, a test that deliberately triggers an
    # IntegrityError leaves the outer transaction deassociated, and teardown
    # warns. With it, the failed statement rolls back to the savepoint and the
    # outer transaction stays valid for a clean rollback.
    factory = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = factory()
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()


@pytest_asyncio.fixture
async def api(app: FastAPI, db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """A client whose requests run inside the test's transaction.

    Overriding the session dependency is what makes an API test rollback-safe:
    without it each request would open its own connection and commit, leaving
    rows behind for the next test to trip over.
    """
    from app.db.session import get_session

    async def _session_override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _session_override
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_session, None)
