"""
R microservice client.

FastAPI calls the plumber service via this async HTTP client rather than
importing R models directly. This enforces the microservice boundary and keeps
the Python and R processes fully isolated (separate memory, restartable
independently, individually scalable).

Design decisions:
  - Uses the same httpx.AsyncClient pattern as the ETL layer for consistency.
  - A module-level client singleton is created at startup and closed at
    shutdown via FastAPI lifespan events (see main.py).
  - All calls are wrapped with tenacity retry so a cold-starting R container
    doesn't cause the first request to fail — it just retries with back-off.
  - The ForecastCache (in-memory TTL cache) means common scope+model combos
    are served from Python RAM without hitting R at all.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from api.config import settings

# ---------------------------------------------------------------------------
# Module-level client — managed by FastAPI lifespan
# ---------------------------------------------------------------------------

_client: httpx.AsyncClient | None = None


def get_r_client() -> httpx.AsyncClient:
    """Return the shared httpx client. Raises if not initialised."""
    if _client is None:
        raise RuntimeError("R service client is not initialised. Check lifespan setup.")
    return _client


def init_r_client() -> None:
    global _client
    _client = httpx.AsyncClient(
        base_url=settings.r_service_url,
        timeout=settings.r_service_timeout,
        headers={"Content-Type": "application/json"},
    )


async def close_r_client() -> None:
    global _client
    if _client:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

_retry = retry(
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException)),
    wait=wait_exponential(multiplier=1, min=1, max=15),
    stop=stop_after_attempt(4),
)


# ---------------------------------------------------------------------------
# Thin TTL in-memory cache (avoids hammering R on repeated identical requests)
# ---------------------------------------------------------------------------


class _TTLCache:
    """
    Simple dict-based TTL cache. Not thread-safe but asyncio is single-threaded
    so no lock needed. For production, replace with Redis.
    """

    def __init__(self, ttl_seconds: int = 300):
        self._store: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry and time.monotonic() - entry[1] < self._ttl:
            return entry[0]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.monotonic())

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)


forecast_cache = _TTLCache(ttl_seconds=int(300))  # 5-minute cache


# ---------------------------------------------------------------------------
# R service API wrappers
# ---------------------------------------------------------------------------


@_retry
async def call_forecast(
    scope: str,
    model: str,
    series: list[dict],
    horizon: int = 30,
    conf_level: float = 0.95,
) -> dict:
    """
    POST /forecast to the R plumber service.

    Args:
        scope:      Geographic scope label (for caching + metadata only).
        model:      "arima" | "prophet" | "holtwinters" | "ensemble"
        series:     List of {"ds": "YYYY-MM-DD", "y": float} dicts.
        horizon:    Days ahead to forecast.
        conf_level: Confidence interval width.

    Returns:
        Parsed JSON response from the R service.
    """
    cache_key = f"{scope}:{model}:{horizon}:{conf_level}:{len(series)}"
    cached = forecast_cache.get(cache_key)
    if cached:
        cached["meta"]["cached"] = True
        return cached  # type: ignore[no-any-return]

    client = get_r_client()
    resp = await client.post(
        "/forecast",
        json={
            "scope": scope,
            "model": model,
            "horizon": horizon,
            "conf_level": conf_level,
            "data": series,
        },
    )
    resp.raise_for_status()
    result: dict = resp.json()
    forecast_cache.set(cache_key, result)
    return result


@_retry
async def call_smooth(series: list[dict], window: int = 7) -> dict:
    """POST /smooth to the R plumber service."""
    client = get_r_client()
    resp = await client.post("/smooth", json={"data": series, "window": window})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


@_retry
async def call_correlation(
    covid_data: list[dict],
    economics_data: list[dict],
) -> dict:
    """
    POST /correlation to the R plumber service.

    Args:
        covid_data:     List of {"date": "YYYY-MM-DD", "cases": int}.
        economics_data: List of {"date": "YYYY-MM-DD", "indicator": str, "value": float}.
    """
    client = get_r_client()
    resp = await client.post(
        "/correlation",
        json={"covid": covid_data, "economics": economics_data},
    )
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


@_retry
async def check_r_health() -> dict:
    """GET /health — used by the FastAPI /health endpoint."""
    client = get_r_client()
    resp = await client.get("/health")
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]
