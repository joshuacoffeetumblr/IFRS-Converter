"""The spec §24 disclaimer is an API-level guarantee, not a UI detail."""

from __future__ import annotations

from httpx import AsyncClient


async def test_disclaimer_is_served_by_the_api(client: AsyncClient) -> None:
    response = await client.get("/api/meta/disclaimer")

    assert response.status_code == 200
    body = response.json()
    assert body["disclaimer_en"].startswith("This analysis is an IFRS 18")
    assert "does not constitute accounting advice" in body["disclaimer_en"]
    assert body["disclaimer_ko"]


async def test_scope_limitations_are_stated(client: AsyncClient) -> None:
    """Open question Q7: MPM and OCI exclusions must be stated, not omitted."""
    response = await client.get("/api/meta/disclaimer")

    limitations = " ".join(response.json()["limitations_en"])
    assert "MPM" in limitations
    assert "other comprehensive income" in limitations
    assert "profit or loss only" in limitations
