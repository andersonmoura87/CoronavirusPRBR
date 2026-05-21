# =============================================================================
# api/Dockerfile — FastAPI application
#
# Multi-stage build:
#   Stage 1 (deps)    — installs Python dependencies into a virtual-env layer.
#   Stage 2 (runtime) — copies only the venv + source, no build tools.
#
# Design decisions:
#   - python:3.11-slim keeps the image under 200 MB.
#   - Non-root user (appuser) follows container security best practices.
#   - PYTHONDONTWRITEBYTECODE + PYTHONUNBUFFERED are standard for containers.
#   - The ETL package is copied to /app/etl and added to PYTHONPATH so the
#     API can import shared SQLAlchemy models without duplication.
# =============================================================================

# ── Stage 1: dependency installation ─────────────────────────────────────────
FROM python:3.11-slim AS deps

WORKDIR /build

# System deps for asyncpg (libpq) and httpx (libssl)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY api/requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app"

# Copy virtual environment from deps stage
COPY --from=deps /opt/venv /opt/venv

# Non-root user
RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app
RUN chown appuser:appuser /app

# Copy application source — paths relative to repo root build context
COPY --chown=appuser:appuser api/ ./api/
# Copy shared ETL models (SQLAlchemy ORM + config)
COPY --chown=appuser:appuser etl/ ./etl/

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" \
    || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
