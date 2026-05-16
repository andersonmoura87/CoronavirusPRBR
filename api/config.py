"""
FastAPI application settings.

Uses pydantic-settings to read values from environment variables (or a .env
file in development). The Settings class is instantiated once at module level
and imported everywhere as `from api.config import settings`.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application metadata ──────────────────────────────────────────────
    app_name: str = "Pandemic Data Platform"
    app_version: str = "1.0.0"
    environment: str = Field(default="development", alias="ENVIRONMENT")

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = Field(alias="DATABASE_URL")

    # ── R microservice ────────────────────────────────────────────────────
    r_service_url: str = Field(default="http://r-service:8001", alias="R_SERVICE_URL")
    r_service_timeout: float = Field(default=120.0, alias="R_SERVICE_TIMEOUT")

    # ── CORS ──────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins.
    # "*" is safe for a portfolio project; restrict in production.
    cors_origins: list[str] = Field(
        default=["*"],
        alias="CORS_ORIGINS",
    )

    # ── Prometheus metrics ────────────────────────────────────────────────
    metrics_enabled: bool = Field(default=True, alias="METRICS_ENABLED")

    # ── Pagination defaults ───────────────────────────────────────────────
    default_page_size: int = 1000
    max_page_size: int = 5000


settings = Settings()  # type: ignore[call-arg]
