"""
Vaccination service — queries the vaccination_records table.

Returns daily aggregated counts grouped by dose type so the dashboard can
render a stacked-bar chart of dose 1 / dose 2 / booster without any
additional client-side computation.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import case, func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from etl.models import VaccinationRecord


async def get_vaccination_summary(
    session: AsyncSession,
    state: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = 500,
    offset: int = 0,
) -> dict:
    """
    Return daily vaccination totals pivoted by dose.

    The pivot is done in SQL (CASE + SUM) so we never load the raw event
    rows into Python memory — the vaccination table can have millions of rows.
    """
    filters = []
    if state:
        filters.append(VaccinationRecord.state == state.upper())
    if start_date:
        filters.append(VaccinationRecord.date >= start_date)
    if end_date:
        filters.append(VaccinationRecord.date <= end_date)

    where_clause = and_(*filters) if filters else True

    # Pivot: one row per (date, state) with aggregated dose counts
    pivot_stmt = (
        select(
            VaccinationRecord.date,
            VaccinationRecord.state,
            func.sum(
                case((VaccinationRecord.dose == "1", VaccinationRecord.count), else_=0)
            ).label("dose_1"),
            func.sum(
                case((VaccinationRecord.dose == "2", VaccinationRecord.count), else_=0)
            ).label("dose_2"),
            func.sum(
                case((VaccinationRecord.dose == "R", VaccinationRecord.count), else_=0)
            ).label("dose_reforco"),
            func.sum(VaccinationRecord.count).label("total"),
        )
        .where(where_clause)
        .group_by(VaccinationRecord.date, VaccinationRecord.state)
        .order_by(VaccinationRecord.date.desc())
        .limit(limit)
        .offset(offset)
    )

    rows = (await session.execute(pivot_stmt)).all()

    count_stmt = (
        select(func.count())
        .select_from(
            select(VaccinationRecord.date, VaccinationRecord.state)
            .where(where_clause)
            .group_by(VaccinationRecord.date, VaccinationRecord.state)
            .subquery()
        )
    )
    total = (await session.execute(count_stmt)).scalar_one()

    range_stmt = select(
        func.min(VaccinationRecord.date).label("min_date"),
        func.max(VaccinationRecord.date).label("max_date"),
    ).where(where_clause)
    range_row = (await session.execute(range_stmt)).one()

    data = [
        {
            "date": r.date,
            "state": r.state,
            "dose_1": int(r.dose_1 or 0),
            "dose_2": int(r.dose_2 or 0),
            "dose_reforco": int(r.dose_reforco or 0),
            "total": int(r.total or 0),
        }
        for r in rows
    ]

    return {
        "scope": state or "all",
        "total_records": total,
        "date_range": {
            "start": range_row.min_date.isoformat() if range_row.min_date else None,
            "end": range_row.max_date.isoformat() if range_row.max_date else None,
        },
        "data": data,
    }
