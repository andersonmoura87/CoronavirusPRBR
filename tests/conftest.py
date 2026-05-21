"""
pytest configuration and shared fixtures.

Architecture of the test suite:
  - Unit tests  — service functions tested with a real in-memory SQLite DB
                  (via SQLAlchemy async) so no external Postgres is needed.
  - Integration tests — FastAPI TestClient with a real Postgres (provided as a
                  GitHub Actions service container) and a mocked R service.
  - The R service is always mocked in tests: we test the Python integration
    layer (r_client.py, forecast_service.py), not the R models themselves.
    The R models have their own test coverage in r-service/tests/ (R testthat).

Fixtures:
  engine          — async SQLAlchemy engine (in-memory SQLite for unit tests)
  db_session      — async session connected to the test engine
  test_app        — FastAPI app with DB and R service overrides
  client          — HTTPX TestClient for integration tests
  mock_r_service  — httpx mock that intercepts calls to R service endpoints
  sample_cases    — a list of CovidCase ORM objects for seeding tests
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from etl.models import Base, CovidCase, EconomicIndicator
from api.main import app
from api.dependencies import get_db

# ---------------------------------------------------------------------------
# Event loop policy (required for pytest-asyncio with SQLAlchemy async)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# Test database — SQLite in-memory (no Postgres needed for unit tests)
# ---------------------------------------------------------------------------
TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create a fresh SQLite engine + schema for each test function.

    StaticPool ensures every async session (the fixture session AND the
    sessions created by the FastAPI dependency override inside test_app)
    share the exact same in-memory SQLite connection.  Without it, each
    new connection would get a completely empty database, making
    sample_cases data invisible to the HTTP client.
    """
    eng = create_async_engine(
        TEST_DB_URL,
        echo=False,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine) -> AsyncGenerator[AsyncSession, None]:
    """Async session bound to the test engine."""
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

def make_covid_case(
    state: str = "PR",
    city: str = "Maringá",
    city_ibge_code: str = "4115200",
    offset_days: int = 0,
    confirmed: int = 100,
    deaths: int = 2,
    new_confirmed: int = 10,
    new_deaths: int = 1,
) -> CovidCase:
    # Base date 30 days ago so all fixtures fall within the forecast service's
    # default training window (today - 365 days).  Using a hardcoded past year
    # (e.g. 2021) would place the rows outside the window and cause the
    # "Insufficient historical data" guard to trigger in tests.
    base = date.today() - timedelta(days=30)
    return CovidCase(
        id=uuid.uuid4(),
        state=state,
        city=city,
        city_ibge_code=city_ibge_code,
        place_type="city",
        date=base + timedelta(days=offset_days),
        confirmed=confirmed + offset_days * 10,
        deaths=deaths + offset_days,
        new_confirmed=new_confirmed,
        new_deaths=new_deaths,
        estimated_population=430_000,
        confirmed_per_100k_inhabitants=23.26,
        death_rate=0.02,
    )


def make_economic_indicator(
    code: str = "SELIC",
    value: float = 2.0,
    months_ago: int = 0,
) -> EconomicIndicator:
    ref = date.today().replace(day=1) - timedelta(days=months_ago * 30)
    return EconomicIndicator(
        id=uuid.uuid4(),
        indicator_code=code,
        indicator_name=f"Test {code}",
        source="BCB",
        reference_date=ref,
        value=value,
        unit="%",
    )


@pytest_asyncio.fixture
async def sample_cases(db_session: AsyncSession) -> list[CovidCase]:
    """Insert 30 days of COVID case data for Maringá into the test DB."""
    cases = [make_covid_case(offset_days=i, new_confirmed=20 + i) for i in range(30)]
    db_session.add_all(cases)
    await db_session.commit()
    return cases


@pytest_asyncio.fixture
async def sample_economics(db_session: AsyncSession) -> list[EconomicIndicator]:
    """Insert 12 months of economic indicators."""
    indicators = []
    for i in range(12):
        indicators.append(make_economic_indicator("SELIC", 2.0 + i * 0.5, months_ago=i))
        indicators.append(make_economic_indicator("IPCA", 0.3 + i * 0.05, months_ago=i))
        indicators.append(make_economic_indicator("DESEMPREGO", 14.0 - i * 0.3, months_ago=i))
    db_session.add_all(indicators)
    await db_session.commit()
    return indicators


# ---------------------------------------------------------------------------
# Mock R service — intercepts httpx calls to http://r-service:8001
# ---------------------------------------------------------------------------

MOCK_FORECAST_RESPONSE = {
    "scope": "maringa",
    "model": "prophet",
    "horizon": 30,
    "forecast": [
        {
            "date": str(date.today() + timedelta(days=i)),
            "predicted": 50.0 + i,
            "lower": 40.0 + i,
            "upper": 60.0 + i,
            "model": "prophet",
            "confidence_level": 0.95,
        }
        for i in range(30)
    ],
    "meta": {
        "n_input_rows": 30,
        "elapsed_ms": 800,
        "generated_at": "2024-01-01T00:00:00Z",
        "r_version": "R version 4.3.3 (2024-02-29)",
        "cached": False,
    },
}

MOCK_CORRELATION_RESPONSE = {
    "n_months": 12,
    "date_range": {"start": "2023-01-01", "end": "2023-12-01"},
    "pairwise_correlation": [
        {"indicator": "SELIC", "pearson_r": -0.72, "pearson_p": 0.003,
         "spearman_rho": -0.68, "spearman_p": 0.006, "n_obs": 12}
    ],
    "ols_regression": {"coefficients": [], "glance": {}, "residuals": []},
    "lagged_correlation": [],
    "granger_causality": [
        {"lag": 1, "f_statistic": 4.2, "p_value": 0.04,
         "conclusion": "COVID Granger-causes unemployment (p < 0.05)"}
    ],
    "meta": {"elapsed_ms": 1200, "generated_at": "2024-01-01T00:00:00Z"},
}


@pytest.fixture
def mock_r_client():
    """
    Patch the httpx client used by r_client.py with a mock that returns
    pre-defined JSON responses. Tests never hit the real R service.
    """
    mock_client = AsyncMock()

    async def mock_post(url, **kwargs):
        # httpx Response.json() is SYNCHRONOUS — use MagicMock so that
        # resp.json() returns the dict directly, not a coroutine.
        response = MagicMock()
        response.raise_for_status = lambda: None
        if "/forecast" in url:
            response.json.return_value = MOCK_FORECAST_RESPONSE
        elif "/correlation" in url:
            response.json.return_value = MOCK_CORRELATION_RESPONSE
        else:
            response.json.return_value = {}
        return response

    async def mock_get(url, **kwargs):
        response = MagicMock()
        response.raise_for_status = lambda: None
        response.json.return_value = {"status": "ok", "r_version": "R 4.3.3"}
        return response

    mock_client.post = mock_post
    mock_client.get  = mock_get
    return mock_client


# ---------------------------------------------------------------------------
# FastAPI test client with dependency overrides
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_app(engine, mock_r_client):
    """
    FastAPI app with:
      - DB overridden to use the test SQLite engine
      - R service client overridden to use the mock
    """
    from api.services import r_client as rc
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    original_client = rc._client
    rc._client = mock_r_client

    yield app

    app.dependency_overrides.clear()
    rc._client = original_client


@pytest_asyncio.fixture
async def client(test_app) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX async client wired to the test FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://test",
    ) as ac:
        yield ac
