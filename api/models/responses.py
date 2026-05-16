"""
Pydantic response schemas.

Every public endpoint returns one of these models so:
  - OpenAPI docs are generated automatically and accurately.
  - The Python type-checker catches mismatches between service layer and router.
  - Clients can rely on a stable, documented contract.

Naming convention: <Resource>Response for list wrappers,
                   <Resource>Item for individual rows.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str
    environment: str
    database: str = Field(examples=["connected"])
    r_service: str = Field(examples=["reachable"])
    timestamp: str


# ---------------------------------------------------------------------------
# COVID cases
# ---------------------------------------------------------------------------


class CaseItem(BaseModel):
    date: date
    state: str
    city: Optional[str] = None
    city_ibge_code: Optional[str] = None
    place_type: str
    confirmed: Optional[int] = None
    deaths: Optional[int] = None
    new_confirmed: Optional[int] = None
    new_deaths: Optional[int] = None
    confirmed_per_100k_inhabitants: Optional[float] = None
    death_rate: Optional[float] = None
    estimated_population: Optional[int] = None

    model_config = {"from_attributes": True}


class CasesResponse(BaseModel):
    scope: str = Field(description="Geographic scope: brasil | parana | maringa")
    total_records: int
    date_range: dict[str, str] = Field(
        description="First and last date in the returned dataset",
        examples=[{"start": "2020-03-01", "end": "2023-12-31"}],
    )
    data: list[CaseItem]


# ---------------------------------------------------------------------------
# Vaccination
# ---------------------------------------------------------------------------


class VaccinationItem(BaseModel):
    date: date
    state: str
    city: Optional[str] = None
    city_ibge_code: Optional[str] = None
    vaccine_name: Optional[str] = None
    dose: Optional[str] = None
    count: int

    model_config = {"from_attributes": True}


class VaccinationSummaryItem(BaseModel):
    """Aggregated daily totals per state (used for the summary endpoint)."""

    date: date
    state: str
    dose_1: int = 0
    dose_2: int = 0
    dose_reforco: int = 0
    total: int = 0


class VaccinationResponse(BaseModel):
    scope: str
    total_records: int
    date_range: dict[str, str]
    data: list[VaccinationSummaryItem]


# ---------------------------------------------------------------------------
# Forecast (from R microservice)
# ---------------------------------------------------------------------------


class ForecastPoint(BaseModel):
    date: str = Field(description="ISO-8601 date string")
    predicted: float
    lower: float
    upper: float
    model: str
    confidence_level: float = 0.95


class ForecastMeta(BaseModel):
    n_input_rows: int
    elapsed_ms: int
    generated_at: str
    r_version: str
    cached: bool = False


class ForecastResponse(BaseModel):
    scope: str
    model: str
    horizon: int
    forecast: list[ForecastPoint]
    meta: ForecastMeta


# ---------------------------------------------------------------------------
# Economics
# ---------------------------------------------------------------------------


class EconomicPoint(BaseModel):
    date: str = Field(description="First day of the reference month (ISO-8601)")
    indicator: str = Field(
        description="Indicator code: SELIC | SELIC_META | IPCA_BCB | IPCA | DESEMPREGO"
    )
    value: float
    unit: str


class OlsCoefficient(BaseModel):
    term: str
    estimate: float
    std_error: float
    statistic: float
    p_value: float
    conf_low: Optional[float] = None
    conf_high: Optional[float] = None


class OlsGlance(BaseModel):
    r_squared: float
    adj_r_squared: float
    f_statistic: Optional[float] = None
    p_value: Optional[float] = None
    nobs: int
    aic: Optional[float] = None
    bic: Optional[float] = None


class CorrelationRow(BaseModel):
    indicator: str
    pearson_r: Optional[float] = None
    pearson_p: Optional[float] = None
    spearman_rho: Optional[float] = None
    spearman_p: Optional[float] = None
    n_obs: int


class GrangerRow(BaseModel):
    lag: int
    f_statistic: Optional[float] = None
    p_value: Optional[float] = None
    conclusion: str


class EconomicsResponse(BaseModel):
    series: list[EconomicPoint]
    correlation: list[CorrelationRow]
    ols: dict[str, Any] = Field(
        description="OLS regression output: coefficients, glance, residuals"
    )
    granger: list[GrangerRow]
    meta: dict[str, Any]
