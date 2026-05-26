"""
Cases service — queries the covid_cases table.

Design decisions:
  - All queries use SQLAlchemy Core (select()) rather than ORM session.get(),
    which keeps the SQL explicit and easy to profile in Grafana.
  - The scope→filter mapping lives here, not in the router, so the router
    stays thin and the logic is unit-testable without HTTP context.
  - We return plain dicts from the DB layer and let the router validate them
    into Pydantic models — avoids double ORM overhead.
  - Pagination (limit/offset) is supported on all list endpoints so Streamlit
    can fetch incrementally without hitting memory limits.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select, func, and_, true
from sqlalchemy.ext.asyncio import AsyncSession

from etl.models import CovidCase

# Geographic scope definitions
# Maringá city_ibge_code: 4115200 (IBGE 7-digit code)
SCOPE_FILTERS = {
    "brasil": {},  # no filter → all rows
    "parana": {"state": "PR"},
    "maringa": {"city_ibge_code": "4115200"},
}


async def get_cases(
    session: AsyncSession,
    scope: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    place_type: str = "city",
    limit: int = 1000,
    offset: int = 0,
) -> dict:
    """
    Return COVID-19 case records for the requested geographic scope.

    Args:
        session:    Async SQLAlchemy session (injected via FastAPI Depends).
        scope:      "brasil" | "parana" | "maringa"
        start_date: Filter rows on or after this date.
        end_date:   Filter rows on or before this date.
        place_type: "city" (default) or "state" for state-level aggregates.
        limit:      Max rows returned (capped at 5000 in router).
        offset:     Pagination offset.

    Returns:
        Dict with keys: scope, total_records, date_range, data (list of dicts).
    """
    scope = scope.lower()
    if scope not in SCOPE_FILTERS:
        raise ValueError(f"Unknown scope '{scope}'. Valid: {list(SCOPE_FILTERS)}")

    filters = []
    geo = SCOPE_FILTERS[scope]

    if "state" in geo:
        filters.append(CovidCase.state == geo["state"])
    if "city_ibge_code" in geo:
        filters.append(CovidCase.city_ibge_code == geo["city_ibge_code"])

    filters.append(CovidCase.place_type == place_type)

    if start_date:
        filters.append(CovidCase.date >= start_date)
    if end_date:
        filters.append(CovidCase.date <= end_date)

    where_clause = and_(*filters) if filters else true()

    # Total count (for pagination metadata)
    count_stmt = select(func.count()).select_from(CovidCase).where(where_clause)
    total = (await session.execute(count_stmt)).scalar_one()

    # Date range
    range_stmt = select(
        func.min(CovidCase.date).label("min_date"),
        func.max(CovidCase.date).label("max_date"),
    ).where(where_clause)
    range_row = (await session.execute(range_stmt)).one()

    # Data rows
    data_stmt = (
        select(CovidCase)
        .where(where_clause)
        .order_by(CovidCase.date.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(data_stmt)).scalars().all()

    return {
        "scope": scope,
        "total_records": total,
        "date_range": {
            "start": range_row.min_date.isoformat() if range_row.min_date else None,
            "end": range_row.max_date.isoformat() if range_row.max_date else None,
        },
        "data": [_case_to_dict(r) for r in rows],
    }


async def get_cases_time_series(
    session: AsyncSession,
    scope: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[dict]:
    """
    Return daily new_confirmed values for a scope — used by the forecast
    service to build the historical series sent to the R microservice.
    """
    scope = scope.lower()
    if scope not in SCOPE_FILTERS:
        raise ValueError(f"Unknown scope '{scope}'")

    filters = (
        [CovidCase.place_type == "state"]
        if scope == "brasil"
        else [CovidCase.place_type == "city"]
    )
    geo = SCOPE_FILTERS[scope]

    if "state" in geo:
        filters.append(CovidCase.state == geo["state"])
    if "city_ibge_code" in geo:
        filters.append(CovidCase.city_ibge_code == geo["city_ibge_code"])
    if start_date:
        filters.append(CovidCase.date >= start_date)
    if end_date:
        filters.append(CovidCase.date <= end_date)

    # For brasil scope, sum across all states per day
    if scope == "brasil":
        stmt = (
            select(
                CovidCase.date.label("ds"),
                func.sum(CovidCase.new_confirmed).label("y"),
            )
            .where(and_(*filters))
            .group_by(CovidCase.date)
            .order_by(CovidCase.date)
        )
        rows = (await session.execute(stmt)).all()
        return [{"ds": str(r.ds), "y": r.y or 0} for r in rows]

    # For parana / maringa — aggregate city-level rows per day
    stmt = (
        select(
            CovidCase.date.label("ds"),
            func.sum(CovidCase.new_confirmed).label("y"),
        )
        .where(and_(*filters))
        .group_by(CovidCase.date)
        .order_by(CovidCase.date)
    )
    rows = (await session.execute(stmt)).all()
    return [{"ds": str(r.ds), "y": r.y or 0} for r in rows]


def _case_to_dict(row: CovidCase) -> dict:
    return {
        "date": row.date,
        "state": row.state,
        "city": row.city,
        "city_ibge_code": row.city_ibge_code,
        "place_type": row.place_type,
        "confirmed": row.confirmed,
        "deaths": row.deaths,
        "new_confirmed": row.new_confirmed,
        "new_deaths": row.new_deaths,
        "confirmed_per_100k_inhabitants": row.confirmed_per_100k_inhabitants,
        "death_rate": row.death_rate,
        "estimated_population": row.estimated_population,
    }
