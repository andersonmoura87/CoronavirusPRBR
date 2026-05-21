"""
Forecast service — orchestrates DB query + R microservice call.

This is the only module where the Python and R worlds meet:
  1. Pull historical COVID case series from PostgreSQL.
  2. Send it to the R plumber service via HTTP.
  3. Optionally cache the result in the forecast_results table so the
     dashboard can read pre-computed forecasts without waiting for R.
"""

from __future__ import annotations

from datetime import date, timedelta

try:
    from sqlalchemy.dialects.postgresql import insert
except ImportError:  # pragma: no cover
    from sqlalchemy import insert  # type: ignore[assignment]
from sqlalchemy.ext.asyncio import AsyncSession

from api.services.cases_service import get_cases_time_series
from api.services.r_client import call_forecast
from etl.models import ForecastResult


async def get_forecast(
    session: AsyncSession,
    scope: str,
    model: str = "prophet",
    horizon: int = 30,
    conf_level: float = 0.95,
    training_days: int = 365,
    persist: bool = True,
) -> dict:
    """
    Run a forecast for the requested scope using the R microservice.

    Steps:
      1. Fetch last `training_days` of daily case data from PostgreSQL.
      2. POST the series to the R /forecast endpoint.
      3. Optionally UPSERT results into forecast_results for caching.
      4. Return the structured forecast response.

    Args:
        session:       Async DB session.
        scope:         "brasil" | "parana" | "maringa"
        model:         "arima" | "prophet" | "holtwinters" | "ensemble"
        horizon:       Days ahead to forecast (1–90).
        conf_level:    Confidence interval (0.5–0.99).
        training_days: How many days of history to send to R.
        persist:       If True, write results to forecast_results table.

    Returns:
        Dict matching the ForecastResponse schema.
    """
    # Clamp inputs
    horizon = min(max(horizon, 1), 90)
    conf_level = min(max(conf_level, 0.5), 0.99)
    training_days = min(max(training_days, 30), 730)

    start_date = date.today() - timedelta(days=training_days)
    series = await get_cases_time_series(session, scope, start_date=start_date)

    if len(series) < 14:
        raise ValueError(
            f"Insufficient historical data for scope '{scope}' "
            f"({len(series)} days). Need at least 14."
        )

    result = await call_forecast(
        scope=scope,
        model=model,
        series=series,
        horizon=horizon,
        conf_level=conf_level,
    )

    if persist and not result.get("meta", {}).get("cached", False):
        try:
            await _persist_forecast(session, scope, model, conf_level, result)
        except Exception:
            # Persist is a best-effort cache — failure (e.g. SQLite in tests,
            # or a missing constraint) must never break the forecast response.
            pass

    return result  # type: ignore[no-any-return]


async def _persist_forecast(
    session: AsyncSession,
    scope: str,
    model: str,
    conf_level: float,
    result: dict,
) -> None:
    """Write forecast rows to the forecast_results cache table."""
    rows = [
        {
            "scope": scope,
            "model": model,
            "forecast_date": point["date"],
            "predicted_cases": point["predicted"],
            "lower_bound": point["lower"],
            "upper_bound": point["upper"],
            "confidence_level": conf_level,
            "r_model_version": result.get("meta", {}).get("r_version", "unknown"),
        }
        for point in result.get("forecast", [])
    ]

    if not rows:
        return

    stmt = (
        insert(ForecastResult)
        .values(rows)
        .on_conflict_do_update(
            constraint="uq_forecast_scope_model_date",
            set_={
                "predicted_cases": insert(ForecastResult).excluded.predicted_cases,
                "lower_bound": insert(ForecastResult).excluded.lower_bound,
                "upper_bound": insert(ForecastResult).excluded.upper_bound,
                "r_model_version": insert(ForecastResult).excluded.r_model_version,
            },
        )
    )
    await session.execute(stmt)
    await session.commit()
