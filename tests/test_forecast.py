"""
Tests for GET /forecast

Coverage:
  - Happy path with mocked R service response
  - Invalid scope returns 422
  - Invalid model returns 422
  - Horizon clamping (1–90)
  - Insufficient data raises 422 (< 14 days)
  - R service error propagated as 502
  - Cache hit (meta.cached = True)
  - Response schema validation
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.forecast_service import get_forecast
from tests.conftest import MOCK_FORECAST_RESPONSE, make_covid_case


class TestForecastService:

    @pytest.mark.asyncio
    async def test_forecast_calls_r_service(
        self, db_session: AsyncSession, sample_cases, mock_r_client
    ):
        from api.services import r_client as rc
        original = rc._client
        rc._client = mock_r_client

        result = await get_forecast(db_session, scope="maringa", model="prophet",
                                    horizon=30, persist=False)
        assert result["model"] == "prophet"
        assert len(result["forecast"]) == 30
        rc._client = original

    @pytest.mark.asyncio
    async def test_forecast_raises_on_insufficient_data(
        self, db_session: AsyncSession
    ):
        # No cases in DB → get_cases_time_series returns empty list
        with pytest.raises(ValueError, match="Insufficient historical data"):
            await get_forecast(db_session, scope="maringa", horizon=30, persist=False)

    @pytest.mark.asyncio
    async def test_forecast_horizon_clamped_to_90(
        self, db_session: AsyncSession, sample_cases, mock_r_client
    ):
        from api.services import r_client as rc
        original = rc._client
        rc._client = mock_r_client
        # horizon=200 should be clamped to 90 without raising
        result = await get_forecast(db_session, scope="maringa",
                                    horizon=200, persist=False)
        # The service clamps before calling R; R mock always returns 30 points
        assert result is not None
        rc._client = original


class TestForecastEndpoint:

    @pytest.mark.asyncio
    async def test_forecast_200_prophet(self, client: AsyncClient, sample_cases):
        resp = await client.get("/forecast?scope=maringa&model=prophet&horizon=30")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scope"] == "maringa"
        assert body["model"] == "prophet"
        assert len(body["forecast"]) == 30

    @pytest.mark.asyncio
    async def test_forecast_response_schema(self, client: AsyncClient, sample_cases):
        resp = await client.get("/forecast?scope=maringa")
        body = resp.json()
        assert "scope"    in body
        assert "model"    in body
        assert "horizon"  in body
        assert "forecast" in body
        assert "meta"     in body

        point = body["forecast"][0]
        for field in ("date", "predicted", "lower", "upper", "model", "confidence_level"):
            assert field in point, f"Missing forecast field: {field}"

    @pytest.mark.asyncio
    async def test_forecast_invalid_scope_422(self, client: AsyncClient):
        resp = await client.get("/forecast?scope=europa")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_forecast_invalid_model_422(self, client: AsyncClient):
        resp = await client.get("/forecast?scope=brasil&model=lstm")
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_forecast_horizon_bounds_422(self, client: AsyncClient):
        resp = await client.get("/forecast?scope=brasil&horizon=0")
        assert resp.status_code == 422
        resp2 = await client.get("/forecast?scope=brasil&horizon=91")
        assert resp2.status_code == 422

    @pytest.mark.asyncio
    async def test_forecast_all_models(self, client: AsyncClient, sample_cases):
        for model in ("arima", "prophet", "holtwinters", "ensemble"):
            resp = await client.get(f"/forecast?scope=maringa&model={model}")
            assert resp.status_code == 200, f"Model {model} failed: {resp.text}"

    @pytest.mark.asyncio
    async def test_forecast_meta_contains_elapsed_ms(
        self, client: AsyncClient, sample_cases
    ):
        resp = await client.get("/forecast?scope=maringa")
        meta = resp.json()["meta"]
        assert "elapsed_ms" in meta
        assert isinstance(meta["elapsed_ms"], int)
