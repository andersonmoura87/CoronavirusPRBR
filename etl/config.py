"""
Centralised configuration for the ETL package.

All values are read from environment variables at import time.
This module raises early (with a clear message) if a required variable is
absent, rather than letting the process crash mid-run.

Usage:
    from etl.config import settings
    print(settings.database_url)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Check your .env file or Docker / Kubernetes secret."
        )
    return value


def _optional(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    # -----------------------------------------------------------------------
    # Database
    # -----------------------------------------------------------------------
    # asyncpg DSN for SQLAlchemy async engine
    database_url: str = field(default_factory=lambda: _require("DATABASE_URL"))

    # -----------------------------------------------------------------------
    # brasil.io
    # -----------------------------------------------------------------------
    # Optional: without a token the paginated API works but is rate-limited
    brasilio_token: str = field(default_factory=lambda: _optional("BRASILIO_TOKEN"))
    brasilio_base_url: str = field(
        default_factory=lambda: _optional(
            "BRASILIO_BASE_URL", "https://brasil.io/api/dataset/covid19"
        )
    )

    # -----------------------------------------------------------------------
    # OpenDataSUS
    # -----------------------------------------------------------------------
    # Comma-separated list of two-letter state codes to ingest
    vaccination_states: list[str] = field(
        default_factory=lambda: _optional("VACCINATION_STATES", "PR").upper().split(",")
    )

    # -----------------------------------------------------------------------
    # BCB / IBGE
    # -----------------------------------------------------------------------
    # Years of historical data to backfill on first run
    economics_history_years: int = field(
        default_factory=lambda: int(_optional("ECONOMICS_HISTORY_YEARS", "6"))
    )

    # -----------------------------------------------------------------------
    # HTTP
    # -----------------------------------------------------------------------
    http_timeout_seconds: float = field(
        default_factory=lambda: float(_optional("HTTP_TIMEOUT_SECONDS", "60"))
    )
    http_max_retries: int = field(
        default_factory=lambda: int(_optional("HTTP_MAX_RETRIES", "5"))
    )

    # -----------------------------------------------------------------------
    # Batch sizing
    # -----------------------------------------------------------------------
    # Rows per database UPSERT transaction
    db_batch_size: int = field(
        default_factory=lambda: int(_optional("DB_BATCH_SIZE", "500"))
    )
    # In-memory aggregation flush threshold for vaccination streaming
    vac_flush_threshold: int = field(
        default_factory=lambda: int(_optional("VAC_FLUSH_THRESHOLD", "50000"))
    )

    # -----------------------------------------------------------------------
    # R microservice
    # -----------------------------------------------------------------------
    r_service_url: str = field(
        default_factory=lambda: _optional("R_SERVICE_URL", "http://r-service:8001")
    )
    r_service_timeout: float = field(
        default_factory=lambda: float(_optional("R_SERVICE_TIMEOUT", "120"))
    )


# Singleton — import and use `settings` everywhere in the package
settings = Settings()
