"""
GET /cases/brasil
GET /cases/parana
GET /cases/maringa

Three endpoints, one service — the scope is the only difference.
Using separate paths instead of a single /cases?scope= makes the API
more readable for portfolio viewers and mirrors real-world government APIs
(e.g. IBGE uses geography-scoped URLs).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.models.responses import CasesResponse
from api.services.cases_service import get_cases

router = APIRouter(prefix="/cases", tags=["COVID-19 Cases"])

# Shared query parameters — defined once, reused across the three routes
_DateStart = Annotated[
    Optional[date],
    Query(description="Filter from this date (ISO-8601, inclusive)", example="2021-01-01"),
]
_DateEnd = Annotated[
    Optional[date],
    Query(description="Filter until this date (ISO-8601, inclusive)", example="2022-12-31"),
]
_PlaceType = Annotated[
    str,
    Query(description="'city' for municipality-level data, 'state' for state totals"),
]
_Limit = Annotated[int, Query(ge=1, le=5000, description="Max rows per response")]
_Offset = Annotated[int, Query(ge=0, description="Pagination offset")]


@router.get(
    "/brasil",
    response_model=CasesResponse,
    summary="COVID-19 cases — Brasil",
    description=(
        "Daily COVID-19 confirmed cases and deaths for all Brazilian states. "
        "Source: brasil.io (dados do Ministério da Saúde)."
    ),
)
async def cases_brasil(
    start_date: _DateStart = None,
    end_date: _DateEnd = None,
    place_type: _PlaceType = "state",
    limit: _Limit = 1000,
    offset: _Offset = 0,
    session: AsyncSession = Depends(get_db),
) -> CasesResponse:
    result = await get_cases(
        session, "brasil", start_date, end_date, place_type, limit, offset
    )
    return CasesResponse(**result)


@router.get(
    "/parana",
    response_model=CasesResponse,
    summary="COVID-19 cases — Paraná",
    description=(
        "Daily COVID-19 confirmed cases and deaths for municipalities in Paraná (PR). "
        "Source: brasil.io."
    ),
)
async def cases_parana(
    start_date: _DateStart = None,
    end_date: _DateEnd = None,
    place_type: _PlaceType = "city",
    limit: _Limit = 1000,
    offset: _Offset = 0,
    session: AsyncSession = Depends(get_db),
) -> CasesResponse:
    result = await get_cases(
        session, "parana", start_date, end_date, place_type, limit, offset
    )
    return CasesResponse(**result)


@router.get(
    "/maringa",
    response_model=CasesResponse,
    summary="COVID-19 cases — Maringá",
    description=(
        "Daily COVID-19 confirmed cases and deaths for Maringá (IBGE 4115200). "
        "Source: brasil.io."
    ),
)
async def cases_maringa(
    start_date: _DateStart = None,
    end_date: _DateEnd = None,
    place_type: _PlaceType = "city",
    limit: _Limit = 1000,
    offset: _Offset = 0,
    session: AsyncSession = Depends(get_db),
) -> CasesResponse:
    result = await get_cases(
        session, "maringa", start_date, end_date, place_type, limit, offset
    )
    return CasesResponse(**result)
