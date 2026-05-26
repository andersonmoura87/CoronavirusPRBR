"""
Tests for GET /health

Coverage:
  - Returns 200 when DB and R service are healthy
  - Returns 503 when R service is down
  - Response schema validation
  - Correct version and environment fields
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


class TestHealthEndpoint:

    @pytest.mark.asyncio
    async def test_health_200_when_all_healthy(self, client: AsyncClient):
        resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "connected"

    @pytest.mark.asyncio
    async def test_health_schema(self, client: AsyncClient):
        resp = await client.get("/health")
        body = resp.json()
        for field in ("status", "version", "environment", "database",
                      "r_service", "timestamp"):
            assert field in body, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_health_503_when_r_service_down(
        self, client: AsyncClient
    ):
        import httpx
        from api.services import r_client as rc

        original_get = rc._client.get

        async def raise_connect_error(*args, **kwargs):
            raise httpx.ConnectError("Connection refused")

        rc._client.get = raise_connect_error

        resp = await client.get("/health")
        assert resp.status_code == 503
        assert "error" in resp.json()["r_service"].lower()

        rc._client.get = original_get

    @pytest.mark.asyncio
    async def test_health_root_redirects_to_docs(self, client: AsyncClient):
        resp = await client.get("/", follow_redirects=False)
        assert resp.status_code in (301, 302, 307, 308)
        assert "/docs" in resp.headers.get("location", "")
