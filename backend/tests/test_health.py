from __future__ import annotations

from httpx import AsyncClient


async def test_health_reports_ok(client: AsyncClient) -> None:
    response = await client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "ci"


async def test_openapi_document_is_served(client: AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "IFRS 18 Impact Analyzer API"
