"""Authentication and the error contract."""

from __future__ import annotations

from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.core.config import Settings
from app.core.security import (
    hash_password,
    verify_password,
    verify_password_constant_work,
)

CREDENTIALS = {"email": "preparer@example.com", "password": "correct horse battery"}


async def register(api: AsyncClient, **overrides: object) -> dict[str, object]:
    payload = {**CREDENTIALS, "display_name": "테스트 작성자", **overrides}
    response = await api.post("/api/auth/register", json=payload)
    return {"status": response.status_code, "body": response.json()}


async def token_for(api: AsyncClient) -> str:
    await register(api)
    response = await api.post("/api/auth/login", json=CREDENTIALS)
    assert response.status_code == 200
    return str(response.json()["access_token"])


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def test_a_password_hash_is_salted() -> None:
    """Two identical passwords must not produce the same hash."""
    assert hash_password("same password") != hash_password("same password")


def test_a_correct_password_verifies() -> None:
    encoded = hash_password("correct horse battery")

    assert verify_password("correct horse battery", encoded)
    assert not verify_password("wrong", encoded)


def test_the_hash_carries_its_parameters() -> None:
    """So the cost can be raised later without invalidating existing passwords."""
    prefix, n, r, p, _salt, _key = hash_password("x").split("$")

    assert prefix == "scrypt"
    assert int(n) >= 2**14
    assert int(r) == 8
    assert int(p) == 1


@pytest.mark.parametrize("encoded", [None, "", "not-a-hash", "scrypt$bad", "md5$1$2$3$4$5"])
def test_a_malformed_hash_is_rejected_rather_than_raising(encoded: str | None) -> None:
    assert not verify_password("anything", encoded)


def test_sign_in_does_the_same_work_whether_or_not_the_account_exists() -> None:
    """Otherwise response time enumerates who has an account.

    `verify_password` short-circuits on a missing hash, which is correct for
    callers that already know the account exists. The sign-in path must not.
    """
    import time

    from app.core.security import _dummy_hash

    _dummy_hash()  # a running server would have this warm
    encoded = hash_password("correct horse battery")

    def elapsed(hash_value: str | None) -> float:
        start = time.perf_counter()
        verify_password_constant_work("guess", hash_value)
        return time.perf_counter() - start

    missing = min(elapsed(None) for _ in range(3))
    present = min(elapsed(encoded) for _ in range(3))

    assert Decimal(str(missing / present)) < Decimal("2.0")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_registration_returns_the_account(api: AsyncClient) -> None:
    result = await register(api)

    assert result["status"] == 201
    body = result["body"]
    assert isinstance(body, dict)
    assert body["email"] == CREDENTIALS["email"]
    assert "password" not in body
    assert "password_hash" not in body


async def test_a_short_password_is_refused(api: AsyncClient) -> None:
    result = await register(api, password="short")

    assert result["status"] == 409
    body = result["body"]
    assert isinstance(body, dict)
    assert "at least" in str(body["detail"])


async def test_a_duplicate_email_is_refused(api: AsyncClient) -> None:
    await register(api)

    result = await register(api)

    assert result["status"] == 409


async def test_an_invalid_email_is_refused(api: AsyncClient) -> None:
    result = await register(api, email="not-an-email")

    assert result["status"] == 422


# ---------------------------------------------------------------------------
# Sign-in
# ---------------------------------------------------------------------------


async def test_login_returns_a_bearer_token(api: AsyncClient) -> None:
    await register(api)

    response = await api.post("/api/auth/login", json=CREDENTIALS)

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_at"]


async def test_a_wrong_password_and_an_unknown_account_look_identical(
    api: AsyncClient,
) -> None:
    """The response must not reveal which addresses are registered."""
    await register(api)

    wrong = await api.post(
        "/api/auth/login", json={**CREDENTIALS, "password": "wrong password here"}
    )
    unknown = await api.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "wrong password here"},
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]
    assert wrong.json()["title"] == unknown.json()["title"]


async def test_the_token_identifies_the_caller(api: AsyncClient) -> None:
    token = await token_for(api)

    response = await api.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["email"] == CREDENTIALS["email"]


@pytest.mark.parametrize(
    "header",
    [None, "", "Bearer ", "Bearer not-a-token", "Basic abc", "Bearer a.b.c"],
)
async def test_a_bad_token_is_rejected(api: AsyncClient, header: str | None) -> None:
    headers = {"Authorization": header} if header is not None else {}

    response = await api.get("/api/auth/me", headers=headers)

    assert response.status_code == 401


async def test_an_expired_token_is_rejected(api: AsyncClient) -> None:
    import datetime as dt
    import uuid

    from app.core.security import issue_token

    settings = Settings(environment="ci")
    expired, _ = issue_token(
        uuid.uuid4(), settings, now=dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    )

    response = await api.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# The error contract (RFC 9457)
# ---------------------------------------------------------------------------


async def test_errors_are_problem_documents(api: AsyncClient) -> None:
    response = await api.get("/api/auth/me")

    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 401
    assert body["title"]
    assert body["type"].startswith("https://ifrs18.app/errors/")
    assert body["instance"] == "/api/auth/me"


async def test_validation_errors_name_the_field(api: AsyncClient) -> None:
    response = await api.post("/api/auth/register", json={"email": "x", "password": "y"})

    assert response.status_code == 422
    body = response.json()
    assert body["type"].endswith("validation-failed")
    assert any(error["field"] == "email" for error in body["errors"])
