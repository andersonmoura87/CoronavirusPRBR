"""
GET /health — liveness + readiness probe.

Kubernetes probes this endpoint every 10 s. We check:
  1. Database connectivity (simple SELECT 1).
  2. R microservice reachability (GET /health forwarded to plumber).

If either dependency is down we return HTTP 503 so Kubernetes removes
this pod from the load-balancer rotation until it recovers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.config import settings
from api.dependencies import get_db
from api.models.responses import HealthResponse
from api.services.r_client import check_r_health

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness and readiness probe",
    description=(
        "Returns 200 if both the database and the R microservice are reachable. "
        "Returns 503 if either dependency is unavailable."
    ),
)
async def health_check(session: AsyncSession = Depends(get_db)) -> JSONResponse:
    db_status = "connected"
    r_status = "reachable"
    http_status = 200

    # Database probe
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        db_status = f"error: {exc}"
        http_status = 503

    # R microservice probe
    try:
        await check_r_health()
    except (httpx.HTTPError, Exception) as exc:
        r_status = f"error: {exc}"
        http_status = 503

    body = HealthResponse(
        status="ok" if http_status == 200 else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database=db_status,
        r_service=r_status,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return JSONResponse(content=body.model_dump(), status_code=http_status)
