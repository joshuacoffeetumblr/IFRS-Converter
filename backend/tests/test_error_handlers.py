"""What the API says when something fails that nobody anticipated.

Every deliberate failure in this product already becomes a problem document.
What was missing was the *last* handler: without one, Starlette answers an
unexpected exception with the four bare words ``Internal Server Error``, and a
client has nothing to show but "something went wrong".

That mattered most for the one failure a fresh deployment is likeliest to hit.
An API that cannot reach its database fails on the sign-up request, in exactly
the place a wrong password would — so the only visible evidence pointed at the
form rather than at the deployment, and the first conclusion was that sign-up
was broken.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import InterfaceError, OperationalError

from app.api.errors import install_error_handlers


def app_that_raises(exc: Exception) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return app


async def get(app: FastAPI) -> tuple[int, dict[str, object]]:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")
    return response.status_code, dict(response.json())


def connection_refused() -> OperationalError:
    """What SQLAlchemy raises when the database is not listening."""
    return OperationalError("SELECT 1", {}, ConnectionRefusedError(111, "Connection refused"))


async def test_an_unreachable_database_is_a_503_that_says_so() -> None:
    status_code, body = await get(app_that_raises(connection_refused()))

    assert status_code == 503
    assert body["type"] == "https://ifrs18.app/errors/database-unavailable"
    assert "cannot reach its database" in str(body["detail"])


async def test_an_unreachable_database_says_it_is_not_the_users_input() -> None:
    """The whole point. A 500 here reads as "your password is wrong"."""
    _, body = await get(app_that_raises(connection_refused()))

    assert "not a problem with what you entered" in str(body["detail"])


async def test_a_driver_level_failure_counts_as_unreachable() -> None:
    status_code, body = await get(
        app_that_raises(InterfaceError("SELECT 1", {}, OSError("connection lost")))
    )

    assert status_code == 503
    assert str(body["type"]).endswith("/database-unavailable")


async def test_the_connection_string_is_never_echoed_back() -> None:
    """A DSN carries the database password (spec §32).

    SQLAlchemy puts the failing statement and its parameters in the exception,
    and a handler that echoed `str(exc)` would put them in an HTTP response.
    """
    dsn = "postgresql://ifrs18:hunter2@db.internal:5432/ifrs18"
    status_code, body = await get(app_that_raises(OperationalError(dsn, {}, OSError("nope"))))

    assert status_code == 503
    assert "hunter2" not in str(body)
    assert "db.internal" not in str(body)


async def test_an_unexpected_error_is_a_problem_document_not_bare_text() -> None:
    status_code, body = await get(app_that_raises(ValueError("something odd")))

    assert status_code == 500
    assert body["type"] == "https://ifrs18.app/errors/internal-error"
    assert body["title"] == "Unexpected server error"


async def test_an_unexpected_errors_message_is_not_echoed_back() -> None:
    """It can carry a row, a query or a credential (spec §32)."""
    _, body = await get(app_that_raises(ValueError("매출액 305372914000000 for 삼성전자")))

    assert "305372914000000" not in str(body)
    assert "삼성전자" not in str(body)


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/api/health", 200), ("/api/nope", 404)],
)
async def test_the_catch_all_did_not_swallow_the_ordinary_paths(
    client: AsyncClient, path: str, expected: int
) -> None:
    """A last-resort handler that intercepted everything would hide real 404s."""
    response = await client.get(path)

    assert response.status_code == expected
