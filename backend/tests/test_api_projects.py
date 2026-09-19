"""Projects, and the isolation between them."""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient

PROJECT = {
    "name": "2025 연결 손익계산서",
    "company": {
        "name": "테스트 주식회사",
        "identifier": "1248100998",
        "identifier_scheme": "KR_BRN",
        "jurisdiction": "KR",
    },
    "fiscal_year": 2025,
    "period_start": "2025-01-01",
    "period_end": "2025-12-31",
    "basis": "CONSOLIDATED",
    "presentation_currency": "KRW",
    "presentation_scale": 6,
}


async def sign_up(api: AsyncClient, email: str) -> dict[str, str]:
    password = "correct horse battery"
    await api.post("/api/auth/register", json={"email": email, "password": password})
    response = await api.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def create(api: AsyncClient, headers: dict[str, str], **overrides: Any) -> dict[str, Any]:
    response = await api.post("/api/projects", json={**PROJECT, **overrides}, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


async def test_a_project_is_created_with_its_company(api: AsyncClient) -> None:
    headers = await sign_up(api, "owner@example.com")

    body = await create(api, headers)

    assert body["name"] == PROJECT["name"]
    assert body["company"]["name"] == "테스트 주식회사"
    assert body["status"] == "DRAFT"
    assert body["reconciliation_status"] == "NOT_RUN"


async def test_a_new_project_cannot_be_finalized(api: AsyncClient) -> None:
    """Nothing has been extracted, so there is nothing to finalize."""
    headers = await sign_up(api, "owner@example.com")

    body = await create(api, headers)

    assert body["progress"]["can_finalize"] is False
    assert {reason["code"] for reason in body["progress"]["blocking_reasons"]} == {
        "NOTHING_EXTRACTED"
    }


async def test_the_same_company_is_reused_across_projects(api: AsyncClient) -> None:
    """Matched on the registered identifier, not the name."""
    headers = await sign_up(api, "owner@example.com")

    first = await create(api, headers)
    second = await create(api, headers, name="2024 연결", fiscal_year=2024)

    assert first["company"]["id"] == second["company"]["id"]


async def test_a_company_without_an_identifier_is_not_merged_by_name(
    api: AsyncClient,
) -> None:
    """Names are not unique, so merging on one would join unrelated entities."""
    headers = await sign_up(api, "owner@example.com")
    company = {"name": "같은이름 주식회사", "jurisdiction": "KR"}

    first = await create(api, headers, company=company)
    second = await create(api, headers, company=company, name="다른 프로젝트")

    assert first["company"]["id"] != second["company"]["id"]


async def test_an_identifier_without_a_scheme_is_refused(api: AsyncClient) -> None:
    headers = await sign_up(api, "owner@example.com")

    response = await api.post(
        "/api/projects",
        json={**PROJECT, "company": {"name": "X", "identifier": "123"}},
        headers=headers,
    )

    assert response.status_code == 422


async def test_a_reversed_period_is_refused(api: AsyncClient) -> None:
    headers = await sign_up(api, "owner@example.com")

    response = await api.post(
        "/api/projects",
        json={**PROJECT, "period_start": "2025-12-31", "period_end": "2025-01-01"},
        headers=headers,
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Isolation — the failure that matters most in this product
# ---------------------------------------------------------------------------


async def test_a_project_is_invisible_to_another_user(api: AsyncClient) -> None:
    """One client seeing another's financial statements is the worst outcome here.

    Ownership is applied in the repository query, so a router cannot forget it.
    """
    owner = await sign_up(api, "owner@example.com")
    project = await create(api, owner)
    intruder = await sign_up(api, "intruder@example.com")

    response = await api.get(f"/api/projects/{project['id']}", headers=intruder)

    assert response.status_code == 404


async def test_another_users_project_returns_404_not_403(api: AsyncClient) -> None:
    """403 would confirm the id exists, letting a caller enumerate projects."""
    owner = await sign_up(api, "owner@example.com")
    project = await create(api, owner)
    intruder = await sign_up(api, "intruder@example.com")

    real = await api.get(f"/api/projects/{project['id']}", headers=intruder)
    invented = await api.get(f"/api/projects/{uuid.uuid4()}", headers=intruder)

    assert real.status_code == invented.status_code == 404
    assert real.json()["title"] == invented.json()["title"]


async def test_a_malformed_id_is_also_404(api: AsyncClient) -> None:
    """Same reasoning: the response must not distinguish the kinds of miss."""
    headers = await sign_up(api, "owner@example.com")

    response = await api.get("/api/projects/not-a-uuid", headers=headers)

    assert response.status_code == 404


async def test_listing_shows_only_your_own(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")
    await create(api, owner)
    intruder = await sign_up(api, "intruder@example.com")

    response = await api.get("/api/projects", headers=intruder)

    assert response.status_code == 200
    assert response.json()["items"] == []


async def test_another_user_cannot_delete_your_project(api: AsyncClient) -> None:
    owner = await sign_up(api, "owner@example.com")
    project = await create(api, owner)
    intruder = await sign_up(api, "intruder@example.com")

    response = await api.delete(f"/api/projects/{project['id']}", headers=intruder)

    assert response.status_code == 404
    still_there = await api.get(f"/api/projects/{project['id']}", headers=owner)
    assert still_there.status_code == 200


async def test_every_project_route_requires_authentication(api: AsyncClient) -> None:
    project_id = uuid.uuid4()

    for method, path in [
        ("GET", "/api/projects"),
        ("POST", "/api/projects"),
        ("GET", f"/api/projects/{project_id}"),
        ("DELETE", f"/api/projects/{project_id}"),
    ]:
        response = await api.request(method, path, json=PROJECT if method == "POST" else None)
        assert response.status_code == 401, f"{method} {path}"


# ---------------------------------------------------------------------------
# Listing and deletion
# ---------------------------------------------------------------------------


async def test_listing_is_totally_ordered(api: AsyncClient) -> None:
    """Ordering must be stable even when timestamps tie.

    PostgreSQL's `now()` is transaction time, so rows created in one
    transaction share a `created_at`. Ordering on it alone is not a total
    order, and a cursor built on it would skip or repeat rows at the tie — so
    the query orders by (created_at, id).
    """
    headers = await sign_up(api, "owner@example.com")
    await create(api, headers, name="first")
    await create(api, headers, name="second")

    first_call = await api.get("/api/projects", headers=headers)
    second_call = await api.get("/api/projects", headers=headers)

    names = [item["name"] for item in first_call.json()["items"]]
    assert sorted(names) == ["first", "second"]
    assert first_call.json()["total"] == 2
    assert names == [item["name"] for item in second_call.json()["items"]]


async def test_listing_can_be_filtered_by_status(api: AsyncClient) -> None:
    headers = await sign_up(api, "owner@example.com")
    await create(api, headers)

    matching = await api.get("/api/projects?status=DRAFT", headers=headers)
    other = await api.get("/api/projects?status=FINALIZED", headers=headers)

    assert len(matching.json()["items"]) == 1
    assert other.json()["items"] == []


async def test_deletion_is_soft_and_hides_the_project(api: AsyncClient) -> None:
    """The audit trail still references it, so the row must survive (spec §8)."""
    headers = await sign_up(api, "owner@example.com")
    project = await create(api, headers)

    deleted = await api.delete(f"/api/projects/{project['id']}", headers=headers)

    assert deleted.status_code == 204
    assert (await api.get(f"/api/projects/{project['id']}", headers=headers)).status_code == 404
    assert (await api.get("/api/projects", headers=headers)).json()["items"] == []
