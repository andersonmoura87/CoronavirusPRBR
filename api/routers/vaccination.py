"""GET /vaccination — daily vaccination counts pivoted by dose type."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.responses import VaccinationResponse
from api.services.vaccination_service import get_vaccination_summary

router = APIRouter(tags=["Vaccination"])


@router.get(
    "/vaccination",
    response_model=VaccinationResponse,
    summary="COVID-19 vaccination — daily totals by dose",
    description=(
        "Returns daily vaccination counts grouped by dose type (1ª dose, 2ª dose, reforço). "
        "Data is aggregated per state from OpenDataSUS records. "
        "Filter by state to get municipality-level detail."
    ),
)
async def vaccination(
    state: Annotated[
        Optional[str],
        Query(
            min_length=2,
            max_length=2,
            description="Two-letter state code (e.g. PR, SP)",
        ),
    ] = None,
    start_date: Annotated[
        Optional[date],
        Query(description="Filter from this date (ISO-8601, inclusive)"),
    ] = None,
    end_date: Annotated[
        Optional[date],
        Query(description="Filter until this date (ISO-8601, inclusive)"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: AsyncSession = Depends(get_db),
) -> VaccinationResponse:
    result = await get_vaccination_summary(
        session, state, start_date, end_date, limit, offset
    )
    return VaccinationResponse(**result)
