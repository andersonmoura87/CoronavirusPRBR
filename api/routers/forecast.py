"""
GET /forecast — time-series forecast via R microservice.

This endpoint is intentionally GET (not POST) because:
  - The inputs are small enough to fit in query parameters.
  - GET responses are cacheable by HTTP proxies / CDN (useful for the
    Streamlit dashboard which calls this on every page load).
  - It keeps the public API surface symmetric with the other data endpoints.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.responses import ForecastResponse
from api.services.forecast_service import get_forecast

router = APIRouter(tags=["Forecast"])

_VALID_SCOPES = ("brasil", "parana", "maringa")
_VALID_MODELS = ("arima", "prophet", "holtwinters", "ensemble")


@router.get(
    "/forecast",
    response_model=ForecastResponse,
    summary="Epidemiological forecast via R statistical models",
    description=(
        "Runs a time-series forecast on COVID-19 daily case data using R models "
        "(ARIMA, Prophet, Holt-Winters, or an ensemble of all three). "
        "Historical data is pulled from PostgreSQL; the model runs in the R plumber microservice. "
        "Results are cached in the `forecast_results` table and in-memory (5 min TTL)."
    ),
)
async def forecast(
    scope: Annotated[
        str,
        Query(description="Geographic scope: brasil | parana | maringa"),
    ] = "brasil",
    model: Annotated[
        str,
        Query(description="Forecast model: arima | prophet | holtwinters | ensemble"),
    ] = "prophet",
    horizon: Annotated[
        int,
        Query(ge=1, le=90, description="Days ahead to forecast (1–90)"),
    ] = 30,
    conf_level: Annotated[
        float,
        Query(ge=0.5, le=0.99, description="Confidence interval width (0.5–0.99)"),
    ] = 0.95,
    training_days: Annotated[
        int,
        Query(ge=30, le=730, description="Days of historical data to train on"),
    ] = 365,
    session: AsyncSession = Depends(get_db),
) -> ForecastResponse:
    scope = scope.lower()
    model = model.lower()

    if scope not in _VALID_SCOPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid scope '{scope}'. Valid: {_VALID_SCOPES}",
        )
    if model not in _VALID_MODELS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid model '{model}'. Valid: {_VALID_MODELS}",
        )

    try:
        result = await get_forecast(
            session=session,
            scope=scope,
            model=model,
            horizon=horizon,
            conf_level=conf_level,
            training_days=training_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"R microservice error: {exc}",
        )

    return ForecastResponse(**result)
