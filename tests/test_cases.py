"""
Tests for GET /cases/brasil, /cases/parana, /cases/maringa

Coverage:
  - Happy path: correct status, schema, scope filtering
  - Pagination: limit/offset parameters
  - Date filtering
  - Empty result (no data for scope)
  - Invalid scope returns 404 or redirect
  - Service layer unit test: get_cases() filters correctly
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.cases_service import get_cases, get_cases_time_series
from tests.conftest import make_covid_case


# ---------------------------------------------------------------------------
# Service layer unit tests (no HTTP, direct function calls)
# ---------------------------------------------------------------------------

class TestCasesService:

    @pytest.mark.asyncio
    async def test_get_cases_maringa_filters_by_ibge_code(
        self, db_session: AsyncSession, sample_cases
    ):
        result = await get_cases(db_session, scope="maringa")
        assert result["scope"] == "maringa"
        assert result["total_records"] == 30
        for row in result["data"]:
            assert row["city_ibge_code"] == "4115200"

    @pytest.mark.asyncio
    async def test_get_cases_pagination(
        self, db_session: AsyncSession, sample_cases
    ):
        page1 = await get_cases(db_session, scope="maringa", limit=10, offset=0)
        page2 = await get_cases(db_session, scope="maringa", limit=10, offset=10)

        assert len(page1["data"]) == 10
        assert len(page2["data"]) == 10
        # Pages should not overlap
        dates_p1 = {r["date"] for r in page1["data"]}
        dates_p2 = {r["date"] for r in page2["data"]}
        assert dates_p1.isdisjoint(dates_p2)

    @pytest.mark.asyncio
    async def test_get_cases_date_filter(
        self, db_session: AsyncSession, sample_cases
    ):
        from datetime import date
        result = await get_cases(
            db_session, scope="maringa",
            start_date=date(2021, 6, 1),
            end_date=date(2021, 6, 10),
        )
        assert result["total_records"] == 10
        for row in result["data"]:
            assert date(2021, 6, 1) <= row["date"] <= date(2021, 6, 10)

    @pytest.mark.asyncio
    async def test_get_cases_invalid_scope_raises(
        self, db_session: AsyncSession
    ):
        with pytest.raises(ValueError, match="Unknown scope"):
            await get_cases(db_session, scope="rio_de_janeiro")

    @pytest.mark.asyncio
    async def test_get_cases_time_series_returns_daily_ds_y(
        self, db_session: AsyncSession, sample_cases
    ):
        series = await get_cases_time_series(db_session, scope="maringa")
        assert len(series) > 0
        for row in series:
            assert "ds" in row
            assert "y" in row
            assert row["y"] >= 0

    @pytest.mark.asyncio
    async def test_empty_scope_returns_zero_records(
        self, db_session: AsyncSession
    ):
        # No data inserted for 'brasil' (only Maringá rows in sample_cases)
        result = await get_cases(db_session, scope="brasil", place_type="state")
        assert result["total_records"] == 0
        assert result["data"] == []


# ---------------------------------------------------------------------------
# API endpoint integration tests
# ---------------------------------------------------------------------------

class TestCasesEndpoints:

    @pytest.mark.asyncio
    async def test_cases_maringa_200(self, client: AsyncClient, sample_cases):
        resp = await client.get("/cases/maringa")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "maringa"
        assert body["total_records"] == 30
        assert len(body["data"]) <= 30

    @pytest.mark.asyncio
    async def test_cases_response_schema(self, client: AsyncClient, sample_cases):
        resp = await client.get("/cases/maringa")
        body = resp.json()
        required_keys = {"scope", "total_records", "date_range", "data"}
        assert required_keys.issubset(body.keys())
        assert "start" in body["date_range"]
        assert "end"   in body["date_range"]

    @pytest.mark.asyncio
    async def test_cases_item_has_all_fields(self, client: AsyncClient, sample_cases):
        resp = await client.get("/cases/maringa")
        item = resp.json()["data"][0]
        for field in ("date", "state", "city_ibge_code", "confirmed", "deaths",
                      "new_confirmed", "new_deaths"):
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_cases_limit_respected(self, client: AsyncClient, sample_cases):
        resp = await client.get("/cases/maringa?limit=5")
        assert len(resp.json()["data"]) == 5

    @pytest.mark.asyncio
    async def test_cases_parana_filters_state(self, client: AsyncClient):
        resp = await client.get("/cases/parana")
        assert resp.status_code == 200
        body = resp.json()
        for row in body["data"]:
            assert row["state"] == "PR"

    @pytest.mark.asyncio
    async def test_cases_brasil_200(self, client: AsyncClient):
        resp = await client.get("/cases/brasil")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_cases_invalid_limit_422(self, client: AsyncClient):
        resp = await client.get("/cases/maringa?limit=99999")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_cases_invalid_date_422(self, client: AsyncClient):
        resp = await client.get("/cases/maringa?start_date=not-a-date")
        assert resp.status_code == 422
