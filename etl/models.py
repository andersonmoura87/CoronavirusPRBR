"""
SQLAlchemy ORM models for the pandemic-data-platform.

Design decisions:
- TimescaleDB-compatible schemas (plain PostgreSQL timestamps, compatible with
  the timescaledb extension if the team decides to upgrade later).
- All primary keys are UUIDs generated server-side to avoid hot-spots when
  bulk-inserting from multiple ETL workers.
- Separate tables for cases, vaccination, and economic indicators follow the
  single-responsibility principle and simplify partial refreshes.
- `created_at` / `updated_at` are maintained by SQLAlchemy event listeners so
  the API layer never has to remember to set them.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Mixin helpers
# ---------------------------------------------------------------------------

class TimestampMixin:
    """Automatic created_at / updated_at columns for every table."""

    created_at: datetime = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: datetime = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ---------------------------------------------------------------------------
# COVID-19 case data  (source: brasil.io)
# ---------------------------------------------------------------------------

class CovidCase(Base, TimestampMixin):
    """
    Daily COVID-19 confirmed cases and deaths, aggregated by municipality.

    Granularity: one row per (date, city_ibge_code).
    Cumulative columns mirror the brasil.io schema exactly so a simple UPSERT
    can keep the table in sync without custom diffing logic.
    """

    __tablename__ = "covid_cases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Geographic identifiers
    state = Column(String(2), nullable=False, index=True)
    city = Column(String(255), nullable=True)
    city_ibge_code = Column(String(7), nullable=True, index=True)
    place_type = Column(String(10), nullable=False)  # "city" or "state"

    # Temporal dimension
    epidemiological_week = Column(Integer, nullable=True)
    date = Column(Date, nullable=False, index=True)

    # Epidemiological counters (cumulative)
    confirmed = Column(BigInteger, nullable=True)
    deaths = Column(BigInteger, nullable=True)
    estimated_population = Column(BigInteger, nullable=True)

    # Derived daily deltas (computed during ETL, not by the source)
    new_confirmed = Column(Integer, nullable=True)
    new_deaths = Column(Integer, nullable=True)

    # 7-day rolling average (computed during ETL)
    confirmed_per_100k_inhabitants = Column(Float, nullable=True)
    death_rate = Column(Float, nullable=True)

    __table_args__ = (
        # Natural key: one record per date/place combination
        UniqueConstraint("date", "city_ibge_code", "place_type", name="uq_covid_cases_date_city"),
        # Composite index to speed up the most common API query pattern
        Index("ix_covid_cases_state_date", "state", "date"),
    )


# ---------------------------------------------------------------------------
# Vaccination data  (source: OpenDataSUS)
# ---------------------------------------------------------------------------

class VaccinationRecord(Base, TimestampMixin):
    """
    Individual vaccination events aggregated daily per municipality.

    OpenDataSUS exports are large CSV files; this table stores the pre-aggregated
    daily counts so the API never has to scan raw event logs.
    """

    __tablename__ = "vaccination_records"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    state = Column(String(2), nullable=False, index=True)
    city = Column(String(255), nullable=True)
    city_ibge_code = Column(String(7), nullable=True, index=True)

    date = Column(Date, nullable=False, index=True)

    vaccine_name = Column(String(100), nullable=True)  # CoronaVac, AstraZeneca, etc.
    dose = Column(String(10), nullable=True)            # "1", "2", "R" (reforço)
    count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "date", "city_ibge_code", "vaccine_name", "dose",
            name="uq_vaccination_date_city_vaccine_dose",
        ),
        Index("ix_vaccination_state_date", "state", "date"),
    )


# ---------------------------------------------------------------------------
# Economic indicators  (sources: IBGE API + BCB / SGS API)
# ---------------------------------------------------------------------------

class EconomicIndicator(Base, TimestampMixin):
    """
    Monthly macroeconomic time-series used to model the COVID×economy correlation.

    Each row is one (indicator, reference_date) observation.
    Using a key-value schema (indicator_code + value) rather than wide columns
    makes it trivial to add new indicators without schema migrations.

    indicator_code reference:
        IPCA         — IBGE SIDRA table 1737 (monthly inflation)
        DESEMPREGO   — IBGE PNAD Contínua (unemployment rate)
        SELIC        — BCB SGS series 432 (overnight rate)
        PIB          — IBGE SIDRA table 1846 (GDP quarterly)
    """

    __tablename__ = "economic_indicators"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    indicator_code = Column(String(30), nullable=False, index=True)
    indicator_name = Column(String(200), nullable=False)
    source = Column(String(50), nullable=False)  # "IBGE" or "BCB"

    # Reference period — stored as the first day of the month/quarter
    reference_date = Column(Date, nullable=False, index=True)

    value = Column(Float, nullable=False)
    unit = Column(String(50), nullable=True)   # "%", "R$", "index"
    notes = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("indicator_code", "reference_date", name="uq_economic_indicator_code_date"),
        Index("ix_economic_indicator_code_date", "indicator_code", "reference_date"),
    )


# ---------------------------------------------------------------------------
# Forecast cache  (populated by the R microservice, consumed by FastAPI)
# ---------------------------------------------------------------------------

class ForecastResult(Base, TimestampMixin):
    """
    Pre-computed forecast results from the R plumber microservice.

    Caching strategy: the ETL scheduler refreshes forecasts nightly. FastAPI
    reads from this table rather than calling the R service on every request,
    keeping p99 latency low even when the R container is cold.
    """

    __tablename__ = "forecast_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    scope = Column(String(50), nullable=False, index=True)   # "brasil", "parana", "maringa"
    model = Column(String(30), nullable=False)               # "prophet", "arima", "holtwinters"

    forecast_date = Column(Date, nullable=False)             # the predicted date
    predicted_cases = Column(Float, nullable=True)
    lower_bound = Column(Float, nullable=True)
    upper_bound = Column(Float, nullable=True)
    confidence_level = Column(Float, nullable=True, default=0.95)

    # Metadata for audit / reproducibility
    generated_at = Column(DateTime(timezone=True), server_default=func.now())
    r_model_version = Column(String(20), nullable=True)

    __table_args__ = (
        UniqueConstraint("scope", "model", "forecast_date", name="uq_forecast_scope_model_date"),
        Index("ix_forecast_scope_model", "scope", "model"),
    )
