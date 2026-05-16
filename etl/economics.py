"""
ETL — Macroeconomic indicators ingestion.

Sources:
    1. IBGE SIDRA API — IPCA (inflation) and unemployment (PNAD Contínua)
    2. BCB SGS API   — Selic interest rate

Design decisions:
    - IBGE and BCB both expose well-documented REST APIs that return JSON, so
      no CSV streaming is needed here (datasets are small, monthly granularity).
    - We use a single EconomicIndicator table with a long (key-value) schema
      rather than wide columns — this makes it trivial to add new series later.
    - The BCB SGS API accepts a date range, so we always request the last N
      years and UPSERT, keeping the table incrementally up to date.
    - Reference dates are normalized to the first day of each month so joining
      economic data with COVID case data (also monthly rollups) is a simple
      date equality.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime
from typing import Any

import httpx
import structlog

try:
    from sqlalchemy.dialects.postgresql import insert
except ImportError:  # pragma: no cover
    from sqlalchemy import insert  # type: ignore[assignment]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from etl.models import EconomicIndicator

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ["DATABASE_URL"]

# How many years of historical data to backfill on first run
HISTORY_YEARS = int(os.environ.get("ECONOMICS_HISTORY_YEARS", "6"))

HTTP_TIMEOUT = 30.0
MAX_RETRIES = 5

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=3)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": "pandemic-data-platform/1.0 (portfolio)"},
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    )


@retry(
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    stop=stop_after_attempt(MAX_RETRIES),
)
async def _get_json(client: httpx.AsyncClient, url: str, **kwargs: Any) -> Any:
    resp = await client.get(url, **kwargs)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# BCB SGS API — Banco Central do Brasil
# ---------------------------------------------------------------------------
# Documentation: https://dadosabertos.bcb.gov.br/dataset/taxas-de-juros-basicas-historico
#
# Relevant series:
#   432  — Selic diária (daily overnight rate, annualized)
#   4390 — IPCA (monthly, same series also available from IBGE)
#   28750 — SELIC meta (target rate, monthly decision)

BCB_SGS_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"

BCB_SERIES = {
    "SELIC": {
        "code": 432,
        "name": "Taxa Selic (over/anualizada)",
        "unit": "% a.a.",
    },
    "SELIC_META": {
        "code": 28750,
        "name": "Meta da Taxa Selic",
        "unit": "% a.a.",
    },
    "IPCA_BCB": {
        "code": 433,
        "name": "IPCA (variação mensal)",
        "unit": "%",
    },
}


def _bcb_date_str(d: date) -> str:
    """BCB SGS date format: dd/mm/yyyy."""
    return d.strftime("%d/%m/%Y")


def _bcb_parse_date(raw: str) -> date:
    """Parse dd/mm/yyyy → date."""
    return datetime.strptime(raw, "%d/%m/%Y").date()


async def ingest_bcb_series(
    client: httpx.AsyncClient,
    session: AsyncSession,
    indicator_key: str,
    series_meta: dict,
    start_date: date,
    end_date: date,
) -> int:
    """
    Fetch one BCB SGS time series and UPSERT into economic_indicators.

    The BCB API returns daily values for Selic and monthly for IPCA.
    We group daily Selic observations to monthly averages so the granularity
    matches the other indicators (daily resolution is noise for our use case).
    """
    url = BCB_SGS_URL.format(code=series_meta["code"])
    params = {
        "formato": "json",
        "dataInicial": _bcb_date_str(start_date),
        "dataFinal": _bcb_date_str(end_date),
    }

    log.info("bcb.fetch.start", indicator=indicator_key, url=url)
    data = await _get_json(client, url, params=params)

    if not data:
        log.warning("bcb.fetch.empty", indicator=indicator_key)
        return 0

    # Aggregate daily → monthly mean
    monthly: dict[date, list[float]] = {}
    for item in data:
        try:
            raw_date = _bcb_parse_date(item["data"])
            value = float(item["valor"].replace(",", "."))
            # Normalize to first day of month
            month_key = raw_date.replace(day=1)
            monthly.setdefault(month_key, []).append(value)
        except (KeyError, ValueError) as exc:
            log.warning("bcb.parse_error", item=item, error=str(exc))
            continue

    rows = [
        {
            "indicator_code": indicator_key,
            "indicator_name": series_meta["name"],
            "source": "BCB",
            "reference_date": month_date,
            "value": sum(values) / len(values),  # monthly mean
            "unit": series_meta["unit"],
            "notes": f"BCB SGS series {series_meta['code']}",
        }
        for month_date, values in sorted(monthly.items())
    ]

    if rows:
        stmt = (
            insert(EconomicIndicator)
            .values(rows)
            .on_conflict_do_update(
                constraint="uq_economic_indicator_code_date",
                set_={
                    "value": insert(EconomicIndicator).excluded.value,
                    "updated_at": datetime.utcnow(),
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

    log.info("bcb.fetch.done", indicator=indicator_key, rows=len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# IBGE SIDRA API — inflation (IPCA) and unemployment
# ---------------------------------------------------------------------------
# Documentation: https://apisidra.ibge.gov.br/
#
# Table 1737 — IPCA (monthly national CPI)
# Table 6381 — PNAD Contínua unemployment rate (quarterly)

SIDRA_BASE = "https://apisidra.ibge.gov.br/values"

IBGE_SERIES = {
    "IPCA": {
        "table": 1737,
        "variable": 2266,  # IPCA variação mensal
        "territorial_level": "1",  # Brasil
        "ibge_territorial_code": "all",
        "period": "all",  # fetched with date filter via classification
        "unit": "%",
        "name": "IPCA (variação mensal)",
    },
    "DESEMPREGO": {
        "table": 6381,
        "variable": 4099,  # Taxa de desocupação
        "territorial_level": "1",
        "ibge_territorial_code": "all",
        "period": "all",
        "unit": "%",
        "name": "Taxa de desemprego (PNAD Contínua)",
    },
}


async def _fetch_sidra_table(
    client: httpx.AsyncClient,
    table_id: int,
    variable: int,
    territorial_level: str,
    ibge_territorial_code: str,
    periods: str,
) -> list[dict]:
    """
    Call the IBGE SIDRA API using the positional URL schema.

    The SIDRA URL format is:
        /values/t/{table}/n{level}/{code}/v/{variable}/p/{period}
    """
    url = (
        f"{SIDRA_BASE}"
        f"/t/{table_id}"
        f"/n{territorial_level}/{ibge_territorial_code}"
        f"/v/{variable}"
        f"/p/{periods}"
        f"/f/u"  # units included
    )
    log.info("ibge.sidra.fetch", url=url)
    data = await _get_json(client, url)
    return data if isinstance(data, list) else []


def _sidra_parse_period(period_str: str) -> date | None:
    """
    Parse IBGE period codes to a reference date (first day of period).

    Monthly: "202101" → 2021-01-01
    Quarterly: "2021T1" → 2021-01-01
    """
    period_str = period_str.strip()
    try:
        if "T" in period_str:
            # Quarterly: AAAATQ
            year, quarter = period_str.split("T")
            month = (int(quarter) - 1) * 3 + 1
            return date(int(year), month, 1)
        elif len(period_str) == 6 and period_str.isdigit():
            # Monthly: AAAAMM
            return date(int(period_str[:4]), int(period_str[4:]), 1)
    except (ValueError, IndexError):
        pass
    return None


async def ingest_ibge_ipca(
    client: httpx.AsyncClient,
    session: AsyncSession,
    start_date: date,
    end_date: date,
) -> int:
    """Fetch IPCA monthly inflation from IBGE SIDRA table 1737."""
    meta = IBGE_SERIES["IPCA"]

    # SIDRA period filter: "201901-202312" (month range)
    period_filter = f"{start_date.strftime('%Y%m')}-{end_date.strftime('%Y%m')}"

    raw = await _fetch_sidra_table(
        client,
        table_id=int(meta["table"]),  # type: ignore[arg-type]
        variable=int(meta["variable"]),  # type: ignore[arg-type]
        territorial_level=str(meta["territorial_level"]),
        ibge_territorial_code=str(meta["ibge_territorial_code"]),
        periods=period_filter,
    )

    # SIDRA returns the first row as a header descriptor; skip it
    rows = []
    for item in raw[1:]:
        period_val = item.get("D3C") or item.get("D2C") or ""
        value_str = item.get("V", "").replace(",", ".").strip()

        ref_date = _sidra_parse_period(period_val)
        if not ref_date or not value_str or value_str in ("...", "-"):
            continue

        try:
            rows.append(
                {
                    "indicator_code": "IPCA",
                    "indicator_name": meta["name"],
                    "source": "IBGE",
                    "reference_date": ref_date,
                    "value": float(value_str),
                    "unit": meta["unit"],
                    "notes": f"IBGE SIDRA tabela {meta['table']} variável {meta['variable']}",
                }
            )
        except ValueError as exc:
            log.warning("ibge.ipca.parse_error", item=item, error=str(exc))

    if rows:
        stmt = (
            insert(EconomicIndicator)
            .values(rows)
            .on_conflict_do_update(
                constraint="uq_economic_indicator_code_date",
                set_={
                    "value": insert(EconomicIndicator).excluded.value,
                    "updated_at": datetime.utcnow(),
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

    log.info("ibge.ipca.done", rows=len(rows))
    return len(rows)


async def ingest_ibge_unemployment(
    client: httpx.AsyncClient,
    session: AsyncSession,
    start_date: date,
    end_date: date,
) -> int:
    """
    Fetch unemployment rate from IBGE SIDRA table 6381 (PNAD Contínua).

    PNAD is quarterly, so each record is assigned to the first month of
    the respective quarter to align with monthly COVID data.
    """
    meta = IBGE_SERIES["DESEMPREGO"]

    # PNAD quarters: build the period string "2019T1-2023T4"
    start_quarter = _date_to_quarter_str(start_date)
    end_quarter = _date_to_quarter_str(end_date)
    period_filter = f"{start_quarter}-{end_quarter}"

    raw = await _fetch_sidra_table(
        client,
        table_id=int(meta["table"]),  # type: ignore[arg-type]
        variable=int(meta["variable"]),  # type: ignore[arg-type]
        territorial_level=str(meta["territorial_level"]),
        ibge_territorial_code=str(meta["ibge_territorial_code"]),
        periods=period_filter,
    )

    rows = []
    for item in raw[1:]:
        period_val = item.get("D3C") or item.get("D2C") or ""
        value_str = item.get("V", "").replace(",", ".").strip()

        ref_date = _sidra_parse_period(period_val)
        if not ref_date or not value_str or value_str in ("...", "-"):
            continue

        try:
            rows.append(
                {
                    "indicator_code": "DESEMPREGO",
                    "indicator_name": meta["name"],
                    "source": "IBGE",
                    "reference_date": ref_date,
                    "value": float(value_str),
                    "unit": meta["unit"],
                    "notes": f"IBGE SIDRA tabela {meta['table']} variável {meta['variable']}",
                }
            )
        except ValueError as exc:
            log.warning("ibge.unemployment.parse_error", item=item, error=str(exc))

    if rows:
        stmt = (
            insert(EconomicIndicator)
            .values(rows)
            .on_conflict_do_update(
                constraint="uq_economic_indicator_code_date",
                set_={
                    "value": insert(EconomicIndicator).excluded.value,
                    "updated_at": datetime.utcnow(),
                },
            )
        )
        await session.execute(stmt)
        await session.commit()

    log.info("ibge.unemployment.done", rows=len(rows))
    return len(rows)


# ---------------------------------------------------------------------------
# Helper: build combined economics summary for the API layer
# ---------------------------------------------------------------------------


async def fetch_economics_summary(session: AsyncSession) -> list[dict]:
    """
    Return a combined economics time-series aligned by month.

    This is used by the FastAPI /economics endpoint to avoid complex joins
    at query time — the ETL pre-joins and the API just reads.
    """
    from sqlalchemy import select
    from etl.models import EconomicIndicator

    result = await session.execute(
        select(
            EconomicIndicator.reference_date,
            EconomicIndicator.indicator_code,
            EconomicIndicator.value,
            EconomicIndicator.unit,
        ).order_by(
            EconomicIndicator.indicator_code,
            EconomicIndicator.reference_date,
        )
    )

    rows = result.fetchall()
    return [
        {
            "date": r.reference_date.isoformat(),
            "indicator": r.indicator_code,
            "value": r.value,
            "unit": r.unit,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _date_to_quarter_str(d: date) -> str:
    """Convert a date to IBGE quarter string: 2021-05-01 → '2021T2'."""
    quarter = (d.month - 1) // 3 + 1
    return f"{d.year}T{quarter}"


def _history_start() -> date:
    """First day of the month N years ago."""
    today = date.today()
    start_year = today.year - HISTORY_YEARS
    return date(start_year, 1, 1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_all() -> None:
    """Fetch all economic indicators and persist to database."""
    start = _history_start()
    end = date.today()

    log.info("economics.ingest.start", start=start.isoformat(), end=end.isoformat())

    async with build_client() as client:
        async with AsyncSessionLocal() as session:
            # BCB series
            for key, meta in BCB_SERIES.items():
                await ingest_bcb_series(client, session, key, meta, start, end)

            # IBGE series
            await ingest_ibge_ipca(client, session, start, end)
            await ingest_ibge_unemployment(client, session, start, end)

    log.info("economics.ingest.done")


if __name__ == "__main__":
    asyncio.run(run_all())
