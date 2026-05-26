"""
GET /economics — COVID-19 × macroeconomic indicators correlation analysis.

Fetches SELIC, IPCA, and unemployment from the DB, joins them with COVID
case counts, and delegates the statistical analysis (OLS, CCF, Granger) to
the R microservice. Returns both the raw series and the model outputs.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.responses import EconomicsResponse
from api.services.economics_service import get_economics_with_correlation

router = APIRouter(tags=["Economics"])


@router.get(
    "/economics",
    response_model=EconomicsResponse,
    summary="COVID-19 × macroeconomics correlation analysis",
    description=(
        "Returns monthly macroeconomic time-series (Selic, IPCA, unemployment) "
        "alongside a full statistical analysis of their correlation with COVID-19 cases. "
        "Analysis includes Pearson/Spearman correlations, OLS regression, "
        "lagged cross-correlations (±6 months), and Granger causality tests. "
        "Statistical computation is performed by the R plumber microservice."
    ),
)
async def economics(
    scope: Annotated[
        str,
        Query(description="COVID scope for correlation: brasil | parana | maringa"),
    ] = "brasil",
    start_date: Annotated[
        Optional[date],
        Query(description="Filter from this date (ISO-8601)"),
    ] = None,
    end_date: Annotated[
        Optional[date],
        Query(description="Filter until this date (ISO-8601)"),
    ] = None,
    session: AsyncSession = Depends(get_db),
) -> EconomicsResponse:
    valid_scopes = ("brasil", "parana", "maringa")
    if scope.lower() not in valid_scopes:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid scope '{scope}'. Valid: {valid_scopes}",
        )

    try:
        result = await get_economics_with_correlation(
            session=session,
            start_date=start_date,
            end_date=end_date,
            scope=scope.lower(),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Economics analysis failed: {exc}",
        )

    return EconomicsResponse(**result)
