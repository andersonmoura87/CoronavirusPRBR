"""
Economics service — joins COVID data with economic indicators and
delegates the statistical analysis to the R microservice.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.cases_service import get_cases_time_series
from api.services.r_client import call_correlation
from etl.models import EconomicIndicator


async def get_economics_with_correlation(
    session: AsyncSession,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    scope: str = "brasil",
) -> dict:
    """
    Fetch economic time-series from the DB and run the full correlation
    analysis (Pearson, OLS, lagged CCF, Granger) via the R microservice.

    Returns a dict matching the EconomicsResponse schema.
    """
    # Fetch all economic indicators from DB
    filters = []
    if start_date:
        filters.append(EconomicIndicator.reference_date >= start_date)
    if end_date:
        filters.append(EconomicIndicator.reference_date <= end_date)

    from sqlalchemy import and_
    where_clause = and_(*filters) if filters else True

    eco_stmt = (
        select(EconomicIndicator)
        .where(where_clause)
        .order_by(EconomicIndicator.indicator_code, EconomicIndicator.reference_date)
    )
    eco_rows = (await session.execute(eco_stmt)).scalars().all()

    economics_data = [
        {
            "date": str(r.reference_date),
            "indicator": r.indicator_code,
            "value": r.value,
            "unit": r.unit or "",
        }
        for r in eco_rows
    ]

    # Fetch COVID case series for correlation (monthly granularity handled in R)
    covid_series = await get_cases_time_series(session, scope, start_date, end_date)
    covid_data = [
        {"date": row["ds"], "cases": row["y"]}
        for row in covid_series
        if row["y"] is not None
    ]

    if not covid_data or not economics_data:
        return {
            "series": economics_data,
            "correlation": [],
            "ols": {},
            "granger": [],
            "meta": {"error": "Insufficient data for correlation analysis"},
        }

    # Delegate all statistics to R
    correlation_result = await call_correlation(covid_data, economics_data)

    # Reshape R response to match EconomicsResponse schema
    series_output = [
        {
            "date": row["date"],
            "indicator": row["indicator"],
            "value": row["value"],
            "unit": row.get("unit", ""),
        }
        for row in economics_data
    ]

    return {
        "series": series_output,
        "correlation": _parse_correlation(correlation_result.get("pairwise_correlation", [])),
        "ols": correlation_result.get("ols_regression", {}),
        "granger": _parse_granger(correlation_result.get("granger_causality", [])),
        "meta": correlation_result.get("meta", {}),
    }


def _parse_correlation(raw: list | dict) -> list[dict]:
    if isinstance(raw, dict):
        # R data.frame serialised as {col: {0: val, 1: val}}
        if not raw:
            return []
        keys = list(raw.keys())
        n = len(raw[keys[0]])
        return [
            {k: raw[k].get(str(i)) for k in keys}
            for i in range(n)
        ]
    return raw if isinstance(raw, list) else []


def _parse_granger(raw: list | dict) -> list[dict]:
    if isinstance(raw, dict) and "error" in raw:
        return [{"lag": 0, "f_statistic": None, "p_value": None, "conclusion": raw["error"]}]
    if isinstance(raw, dict):
        keys = list(raw.keys())
        if not keys:
            return []
        n = len(raw[keys[0]])
        return [{k: raw[k].get(str(i)) for k in keys} for i in range(n)]
    return raw if isinstance(raw, list) else []
