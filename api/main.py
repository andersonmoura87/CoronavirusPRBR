"""
pandemic-data-platform — FastAPI application entry point.

Architecture overview:
  ┌─────────────┐    HTTP     ┌─────────────────┐    SQL    ┌────────────┐
  │  Streamlit  │ ──────────► │   FastAPI (8000) │ ────────► │ PostgreSQL │
  │  dashboard  │             │                 │           │ (Supabase) │
  └─────────────┘             │   /cases        │           └────────────┘
                              │   /vaccination  │
                              │   /forecast  ───┼──► R plumber (8001)
                              │   /economics ───┘
                              │   /health       │
                              └─────────────────┘

Design decisions:
  - Single lifespan context manager handles startup + shutdown (replaces the
    deprecated on_event decorators).
  - Prometheus metrics are exposed at /metrics via prometheus_fastapi_instrumentator.
    Grafana scrapes this; no extra exporter container needed.
  - Structured JSON logging is configured at startup so every request has a
    correlation ID and can be queried in Grafana Loki.
  - CORS is open (*) for the portfolio; tighten via CORS_ORIGINS env var.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from api.config import settings
from api.routers import cases, economics, forecast, health, vaccination
from api.services.r_client import close_r_client, init_r_client

# ---------------------------------------------------------------------------
# Structured logging setup
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
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
# Lifespan — startup and shutdown hooks
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs before the first request (startup) and after the last (shutdown).
    Manages the shared httpx client for the R microservice.
    """
    log.info("app.startup", version=settings.app_version, env=settings.environment)
    init_r_client()
    yield
    log.info("app.shutdown")
    await close_r_client()


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Complete epidemiological analytics platform for Brazil. "
        "Ingests public health data (brasil.io, OpenDataSUS), runs R statistical "
        "models (ARIMA, Prophet, Holt-Winters) via a plumber microservice, and "
        "exposes a REST API for a Streamlit dashboard."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CORS — explicit security decision
#
# Default: allow_origins=["*"] (open).
#
# Conscious trade-offs for this portfolio project:
#   - There is no authentication layer (no JWT, no API key).
#   - All data is public (brasil.io, OpenDataSUS, IBGE, BCB).
#   - The Streamlit dashboard must call the API from the browser.
#
# Risk accepted: any origin can read this public data. This is equivalent
# to a public government data portal — the data is already freely available.
#
# What this is NOT: a replacement for auth on write endpoints. This API
# has no write endpoints, so CORS-based origin restriction would only add
# friction without improving security.
#
# To restrict to specific origins in production:
#   Set CORS_ORIGINS=https://pandemic.yourdomain.com in the environment.
#   The setting is already wired in api/config.py.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET"],        # read-only API — no POST/PUT/DELETE from browser
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    """Log every request with method, path, status, and duration."""
    t0 = time.perf_counter()
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        method=request.method,
        path=request.url.path,
    )

    response = await call_next(request)

    elapsed_ms = round((time.perf_counter() - t0) * 1000)
    log.info(
        "http.request",
        status=response.status_code,
        elapsed_ms=elapsed_ms,
    )
    response.headers["X-Process-Time-Ms"] = str(elapsed_ms)
    return response


# ---------------------------------------------------------------------------
# Prometheus instrumentation
# ---------------------------------------------------------------------------

if settings.metrics_enabled:
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        excluded_handlers=["/metrics", "/health"],
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------

@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    log.warning("request.validation_error", detail=str(exc))
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.error("request.unhandled_error", error=str(exc), exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check logs for details."},
    )

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health.router)
app.include_router(cases.router)
app.include_router(vaccination.router)
app.include_router(forecast.router)
app.include_router(economics.router)


# ---------------------------------------------------------------------------
# Root redirect → docs
# ---------------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")


# ---------------------------------------------------------------------------
# Dev entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
