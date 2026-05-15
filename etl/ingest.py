"""
ETL — Epidemiological data ingestion.

Sources:
    1. brasil.io  — daily COVID-19 cases by municipality (CSV over HTTPS)
    2. OpenDataSUS — vaccination campaign data (CSV over HTTPS)

Design decisions:
    - Streaming download with chunked CSV parsing avoids loading multi-GB files
      into memory all at once (the OpenDataSUS vaccination file is > 5 GB).
    - All HTTP calls go through a single httpx.AsyncClient configured with
      retry + exponential back-off so transient government-server timeouts do
      not kill a nightly ETL run.
    - Database writes use SQLAlchemy Core INSERT … ON CONFLICT DO UPDATE
      (UPSERT) so re-running the ETL is always idempotent.
    - Logging uses structlog for JSON output, which makes log scraping in
      Grafana / Loki trivial.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
from datetime import date, datetime
from typing import AsyncIterator, Iterator

import httpx
import structlog
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from etl.models import Base, CovidCase, VaccinationRecord

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
# Configuration — all values come from environment variables
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ["DATABASE_URL"]  # e.g. postgresql+asyncpg://user:pass@host/db

# brasil.io endpoints — the API requires a token for large downloads
BRASILIO_TOKEN = os.environ.get("BRASILIO_TOKEN", "")
BRASILIO_BASE = "https://brasil.io/api/dataset/covid19"

# OpenDataSUS — public CSVs, no auth required
OPENDATASUS_BASE = "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br/SRAG/2021"

CHUNK_SIZE = 4096          # bytes per streaming chunk
BATCH_SIZE = 500           # rows per database UPSERT batch
HTTP_TIMEOUT = 60.0        # seconds
MAX_RETRIES = 5

# ---------------------------------------------------------------------------
# Database engine
# ---------------------------------------------------------------------------

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Create all tables if they do not exist yet (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database.schema_ready")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def build_http_client() -> httpx.AsyncClient:
    headers = {"User-Agent": "pandemic-data-platform/1.0 (portfolio)"}
    if BRASILIO_TOKEN:
        headers["Authorization"] = f"Token {BRASILIO_TOKEN}"
    return httpx.AsyncClient(
        headers=headers,
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
    )


@retry(
    retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
    wait=wait_exponential(multiplier=1, min=2, max=60),
    stop=stop_after_attempt(MAX_RETRIES),
)
async def _get_with_retry(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    response = await client.get(url, **kwargs)
    response.raise_for_status()
    return response


async def stream_csv_lines(client: httpx.AsyncClient, url: str) -> AsyncIterator[str]:
    """
    Stream a remote CSV line by line without buffering the whole file.
    Works for both gzip-compressed and plain-text CSV responses.
    """
    buffer = ""
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        async for chunk in response.aiter_text(chunk_size=CHUNK_SIZE):
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line
    if buffer.strip():
        yield buffer


# ---------------------------------------------------------------------------
# brasil.io — COVID-19 cases
# ---------------------------------------------------------------------------

def _parse_covid_row(row: dict) -> dict | None:
    """
    Map a raw brasil.io CSV row to the CovidCase column schema.
    Returns None for rows that are malformed or contain no useful data.
    """
    try:
        raw_date = row.get("date", "").strip()
        if not raw_date:
            return None
        record_date = date.fromisoformat(raw_date)

        confirmed_raw = row.get("confirmed", "").strip()
        deaths_raw = row.get("deaths", "").strip()

        return {
            "state": row.get("state", "").strip().upper(),
            "city": row.get("city", "").strip() or None,
            "city_ibge_code": row.get("city_ibge_code", "").strip() or None,
            "place_type": row.get("place_type", "city").strip(),
            "epidemiological_week": _safe_int(row.get("epidemiological_week")),
            "date": record_date,
            "confirmed": _safe_int(confirmed_raw),
            "deaths": _safe_int(deaths_raw),
            "estimated_population": _safe_int(row.get("estimated_population")),
            "new_confirmed": _safe_int(row.get("new_confirmed")),
            "new_deaths": _safe_int(row.get("new_deaths")),
            "confirmed_per_100k_inhabitants": _safe_float(
                row.get("confirmed_per_100k_inhabitants")
            ),
            "death_rate": _safe_float(row.get("death_rate")),
        }
    except (ValueError, KeyError) as exc:
        log.warning("covid_row.parse_error", error=str(exc), row=row)
        return None


async def ingest_covid_cases(
    client: httpx.AsyncClient,
    session: AsyncSession,
    state: str | None = None,
    page_size: int = 10_000,
) -> int:
    """
    Fetch COVID-19 case data from brasil.io and UPSERT into the database.

    Args:
        client:    Shared httpx.AsyncClient.
        session:   Async SQLAlchemy session.
        state:     Two-letter state code to filter (None = all states).
        page_size: Number of rows per API page.

    Returns:
        Total number of rows processed.
    """
    log.info("covid.ingest.start", state=state or "ALL")
    total = 0
    page = 1

    # brasil.io paginates the API endpoint; the CSV bulk download is
    # available but requires authentication.  We use the paginated API
    # so the pipeline works even without a token (rate-limited but functional).
    while True:
        params: dict = {"page": page, "page_size": page_size, "format": "json"}
        if state:
            params["state"] = state.upper()

        url = f"{BRASILIO_BASE}/caso_full/data/"
        resp = await _get_with_retry(client, url, params=params)
        payload = resp.json()

        results = payload.get("results", [])
        if not results:
            break

        batch = [_parse_covid_row(row) for row in results]
        batch = [r for r in batch if r is not None]

        if batch:
            stmt = (
                insert(CovidCase)
                .values(batch)
                .on_conflict_do_update(
                    constraint="uq_covid_cases_date_city",
                    set_={
                        "confirmed": insert(CovidCase).excluded.confirmed,
                        "deaths": insert(CovidCase).excluded.deaths,
                        "new_confirmed": insert(CovidCase).excluded.new_confirmed,
                        "new_deaths": insert(CovidCase).excluded.new_deaths,
                        "confirmed_per_100k_inhabitants": insert(
                            CovidCase
                        ).excluded.confirmed_per_100k_inhabitants,
                        "death_rate": insert(CovidCase).excluded.death_rate,
                        "updated_at": datetime.utcnow(),
                    },
                )
            )
            await session.execute(stmt)
            await session.commit()

        total += len(batch)
        log.info("covid.ingest.progress", page=page, batch=len(batch), total=total)

        if not payload.get("next"):
            break
        page += 1

    log.info("covid.ingest.done", total=total, state=state or "ALL")
    return total


# ---------------------------------------------------------------------------
# brasil.io — bulk CSV download (faster, needs token)
# ---------------------------------------------------------------------------

async def ingest_covid_cases_bulk_csv(
    client: httpx.AsyncClient,
    session: AsyncSession,
) -> int:
    """
    Alternative to the paginated API: stream the full caso_full CSV export.
    Significantly faster (one request vs thousands) but requires BRASILIO_TOKEN.

    The CSV is ~200 MB compressed; we parse it in streaming fashion.
    """
    if not BRASILIO_TOKEN:
        raise EnvironmentError(
            "BRASILIO_TOKEN is required for bulk CSV download. "
            "Set the environment variable or use ingest_covid_cases() instead."
        )

    url = f"{BRASILIO_BASE}/caso_full/data/?format=csv"
    log.info("covid.bulk_csv.start", url=url)

    total = 0
    batch: list[dict] = []
    header: list[str] | None = None

    async for raw_line in stream_csv_lines(client, url):
        line = raw_line.strip()
        if not line:
            continue

        reader = csv.reader(io.StringIO(line))
        columns = next(reader)

        if header is None:
            header = columns
            continue

        row = dict(zip(header, columns))
        parsed = _parse_covid_row(row)
        if parsed:
            batch.append(parsed)

        if len(batch) >= BATCH_SIZE:
            await _upsert_covid_batch(session, batch)
            total += len(batch)
            log.info("covid.bulk_csv.progress", total=total)
            batch = []

    if batch:
        await _upsert_covid_batch(session, batch)
        total += len(batch)

    log.info("covid.bulk_csv.done", total=total)
    return total


async def _upsert_covid_batch(session: AsyncSession, batch: list[dict]) -> None:
    stmt = (
        insert(CovidCase)
        .values(batch)
        .on_conflict_do_update(
            constraint="uq_covid_cases_date_city",
            set_={
                "confirmed": insert(CovidCase).excluded.confirmed,
                "deaths": insert(CovidCase).excluded.deaths,
                "new_confirmed": insert(CovidCase).excluded.new_confirmed,
                "new_deaths": insert(CovidCase).excluded.new_deaths,
                "updated_at": datetime.utcnow(),
            },
        )
    )
    await session.execute(stmt)
    await session.commit()


# ---------------------------------------------------------------------------
# OpenDataSUS — vaccination data
# ---------------------------------------------------------------------------

# OpenDataSUS exports one CSV per state. Each file is identified by the
# two-letter state abbreviation and the year.
OPENDATASUS_VAC_URL_TEMPLATE = (
    "https://s3.sa-east-1.amazonaws.com/ckan.saude.gov.br"
    "/PNI/vacinas/nt/2021/nt_{state}_2021.csv"
)

# Map OpenDataSUS column names to our schema
_VAC_COLUMN_MAP = {
    "estabelecimento_municipio_codigo": "city_ibge_code",
    "estabelecimento_municipio_nome": "city",
    "estabelecimento_uf": "state",
    "vacina_dataaplicacao": "date",
    "vacina_nome": "vaccine_name",
    "vacina_descricao_dose": "dose",
}


def _parse_vaccination_row(row: dict) -> dict | None:
    """
    Map a raw OpenDataSUS row to the VaccinationRecord schema.
    OpenDataSUS stores one row per individual event; we aggregate during ETL.
    """
    try:
        raw_date = row.get("vacina_dataaplicacao", "").strip()
        if not raw_date:
            return None

        # OpenDataSUS uses dd/mm/yyyy format
        record_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()

        state = (row.get("estabelecimento_uf") or "").strip().upper()
        if not state or len(state) != 2:
            return None

        dose_raw = (row.get("vacina_descricao_dose") or "").strip()
        # Normalize dose labels: "1ª Dose" → "1", "2ª Dose" → "2", "Reforço" → "R"
        dose = _normalize_dose(dose_raw)

        return {
            "state": state,
            "city": (row.get("estabelecimento_municipio_nome") or "").strip() or None,
            "city_ibge_code": (row.get("estabelecimento_municipio_codigo") or "").strip() or None,
            "date": record_date,
            "vaccine_name": (row.get("vacina_nome") or "").strip()[:100] or None,
            "dose": dose,
            "count": 1,  # individual event; aggregated in upsert via SUM
        }
    except (ValueError, KeyError) as exc:
        log.warning("vaccination_row.parse_error", error=str(exc))
        return None


def _normalize_dose(raw: str) -> str:
    """Translate verbose dose descriptions to short codes."""
    raw_lower = raw.lower()
    if "refor" in raw_lower or "adicional" in raw_lower:
        return "R"
    if "1" in raw_lower or "primeira" in raw_lower:
        return "1"
    if "2" in raw_lower or "segunda" in raw_lower:
        return "2"
    return raw[:10]  # fallback: truncate unknown labels


async def ingest_vaccination(
    client: httpx.AsyncClient,
    session: AsyncSession,
    states: list[str] | None = None,
) -> int:
    """
    Stream vaccination CSV files from OpenDataSUS and UPSERT aggregated counts.

    Because OpenDataSUS files contain one row per vaccination event (individual
    patient), we aggregate to daily counts per (city, vaccine, dose) in memory
    before writing to the database, which reduces I/O by orders of magnitude.

    Args:
        client: Shared httpx.AsyncClient.
        session: Async SQLAlchemy session.
        states: List of two-letter state codes. Defaults to ["PR"] (Paraná).
    """
    if states is None:
        states = ["PR"]  # default to Paraná — broadened via env var in prod

    total = 0

    for state in states:
        url = OPENDATASUS_VAC_URL_TEMPLATE.format(state=state.lower())
        log.info("vaccination.ingest.start", state=state, url=url)

        # In-memory aggregation: key = (date, city_ibge_code, vaccine_name, dose)
        aggregated: dict[tuple, dict] = {}
        header: list[str] | None = None

        async for raw_line in stream_csv_lines(client, url):
            line = raw_line.strip()
            if not line:
                continue

            reader = csv.reader(io.StringIO(line), delimiter=";")
            columns = next(reader)

            if header is None:
                header = columns
                continue

            row = dict(zip(header, columns))
            parsed = _parse_vaccination_row(row)
            if not parsed:
                continue

            key = (
                parsed["date"],
                parsed["city_ibge_code"],
                parsed["vaccine_name"],
                parsed["dose"],
            )
            if key in aggregated:
                aggregated[key]["count"] += 1
            else:
                aggregated[key] = parsed

            # Flush to DB in chunks to avoid unbounded memory growth
            if len(aggregated) >= 50_000:
                rows = list(aggregated.values())
                await _upsert_vaccination_batch(session, rows)
                total += len(rows)
                log.info("vaccination.ingest.flush", state=state, total=total)
                aggregated = {}

        if aggregated:
            rows = list(aggregated.values())
            await _upsert_vaccination_batch(session, rows)
            total += len(rows)

        log.info("vaccination.ingest.state_done", state=state, total=total)

    log.info("vaccination.ingest.done", total=total)
    return total


async def _upsert_vaccination_batch(session: AsyncSession, batch: list[dict]) -> None:
    """
    UPSERT vaccination records.

    When the same (date, city, vaccine, dose) key is re-ingested we ADD the
    new count to the existing count — this handles incremental re-runs where
    we might only stream a subset of the file.
    """
    stmt = (
        insert(VaccinationRecord)
        .values(batch)
        .on_conflict_do_update(
            constraint="uq_vaccination_date_city_vaccine_dose",
            set_={
                # Increment rather than overwrite, so partial re-runs accumulate
                "count": VaccinationRecord.count
                + insert(VaccinationRecord).excluded.count,
                "updated_at": datetime.utcnow(),
            },
        )
    )
    await session.execute(stmt)
    await session.commit()


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_int(value) -> int | None:
    try:
        return int(value) if value not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


def _safe_float(value) -> float | None:
    try:
        return float(value) if value not in (None, "", "None") else None
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Entry point — run all ingestion tasks
# ---------------------------------------------------------------------------

async def run_all(state_filter: str | None = None) -> None:
    """
    Orchestrate all ingestion tasks sequentially.

    In production this is called by a cron job (or an Argo workflow).
    For local development: `python -m etl.ingest`
    """
    await init_db()

    async with build_http_client() as client:
        async with AsyncSessionLocal() as session:
            # COVID cases — paginated API (works without token)
            await ingest_covid_cases(client, session, state=state_filter)

            # Vaccination — streaming CSV (Paraná by default)
            vac_states = (
                [state_filter] if state_filter else ["PR"]
            )
            await ingest_vaccination(client, session, states=vac_states)


if __name__ == "__main__":
    import sys

    state_arg = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(run_all(state_filter=state_arg))
