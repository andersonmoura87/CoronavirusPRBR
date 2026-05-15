"""
dashboard/api_client.py — HTTP client for the FastAPI backend.

Falls back to synthetic demo data when the API is unreachable so the
dashboard remains fully navigable without the backend stack running.
Set DEMO_MODE=false to disable the fallback and show real errors.
"""

from __future__ import annotations

import os
import random
from datetime import date, timedelta
from typing import Any

import httpx
import streamlit as st

API_BASE  = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT   = 8.0          # short timeout — fall through to demo quickly
DEMO_MODE = os.environ.get("DEMO_MODE", "true").lower() != "false"

# ---------------------------------------------------------------------------
# Internal HTTP helper
# ---------------------------------------------------------------------------

def _get(path: str, params: dict | None = None) -> tuple[Any, bool]:
    """
    Returns (data, is_live).
    is_live=True  → real API response
    is_live=False → connection failed
    """
    try:
        resp = httpx.get(f"{API_BASE}{path}", params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json(), True
    except (httpx.RequestError, httpx.HTTPStatusError):
        return None, False


# ---------------------------------------------------------------------------
# Demo-data generators — deterministic synthetic data for portfolio preview
# ---------------------------------------------------------------------------

def _demo_cases(scope: str) -> dict:
    random.seed(42)
    start = date(2020, 3, 1)
    rows = []
    confirmed = deaths = 0
    for i in range(1095):   # ~3 years daily
        d = start + timedelta(days=i)
        # rough epidemic wave shape
        t = i / 1095
        wave = (
            3_000  * max(0, 1 - abs(t - 0.15) / 0.10) +
            25_000 * max(0, 1 - abs(t - 0.40) / 0.12) +
            12_000 * max(0, 1 - abs(t - 0.65) / 0.10) +
            8_000  * max(0, 1 - abs(t - 0.80) / 0.08)
        )
        multiplier = {"parana": 0.055, "maringa": 0.008}.get(scope, 1.0)
        new_c = max(0, int(wave * multiplier * (0.85 + random.random() * 0.30)))
        new_d = max(0, int(new_c * 0.022 * (0.7 + random.random() * 0.6)))
        confirmed += new_c
        deaths    += new_d
        rows.append({
            "date": str(d), "state": "BR", "city": None,
            "confirmed": confirmed, "deaths": deaths,
            "new_confirmed": new_c, "new_deaths": new_d,
        })
    return {"scope": scope, "total": len(rows), "data": rows}


def _demo_vaccination() -> dict:
    random.seed(7)
    start = date(2021, 1, 17)   # Brazil vaccination start
    rows = []
    for i in range(730):
        d = start + timedelta(days=i)
        t = i / 730
        ramp = min(1.0, t * 3)
        base = 80_000 * ramp
        d1 = max(0, int(base * (1.2 - 0.8 * t) * (0.8 + random.random() * 0.4)))
        d2 = max(0, int(base * min(t * 1.5, 1.0) * (0.8 + random.random() * 0.4)))
        dr = max(0, int(base * max(0, t - 0.4) * 0.9 * (0.8 + random.random() * 0.4)))
        rows.append({
            "date": str(d), "state": "PR",
            "dose_1": d1, "dose_2": d2, "dose_reforco": dr,
            "total": d1 + d2 + dr,
        })
    return {"state": "PR", "total": len(rows), "data": rows}


def _demo_forecast(model: str, horizon: int) -> dict:
    import math
    random.seed({"prophet": 1, "arima": 2, "holtwinters": 3, "ensemble": 4}.get(model, 1))
    start = date(2023, 1, 1)
    rows = []
    base = 8_000
    for i in range(horizon):
        d = start + timedelta(days=i)
        decay = math.exp(-0.02 * i)
        pred  = max(0, int(base * decay * (0.9 + random.random() * 0.2)))
        band  = int(pred * (0.15 + i * 0.003))
        rows.append({
            "date": str(d),
            "predicted": pred,
            "lower": max(0, pred - band),
            "upper": pred + band,
            "model": model,
            "confidence_level": 0.95,
        })
    return {
        "scope": "brasil", "model": model, "horizon": horizon,
        "forecast": rows,
        "meta": {"elapsed_ms": 187, "r_version": "4.3.2",
                 "generated_at": str(start), "cached": False},
    }


def _demo_economics() -> dict:
    months = [date(2020, 3, 1) + timedelta(days=30 * i) for i in range(36)]
    series = []
    selic_vals    = [3.75,2.25,2.00,2.00,2.00,2.00,2.00,2.75,4.25,5.25,6.25,7.75,
                     9.25,10.75,11.75,12.75,13.25,13.75,13.75,13.75,13.75,13.75,
                     13.75,13.25,12.75,11.75,10.75,10.50,10.50,10.50,10.50,10.50,10.50,10.50,10.50,10.50]
    ipca_vals     = [0.07,-0.02,0.26,0.26,0.64,0.86,1.35,0.83,0.93,0.31,0.53,0.78,
                     1.16,0.93,0.73,1.25,1.62,1.06,0.67,0.59,-0.29,0.54,0.47,0.40,
                     0.71,0.79,0.91,0.62,0.44,0.38,0.35,0.32,0.29,0.26,0.23,0.21]
    desemp_vals   = [12.9,13.3,13.8,14.4,14.1,13.9,14.7,14.6,14.2,14.6,13.2,11.6,
                     11.1,10.5,9.8,9.3,8.9,8.7,7.9,7.6,8.1,7.8,8.1,7.9,
                     8.4,8.0,7.8,7.4,7.1,6.8,6.9,7.0,6.8,6.7,6.5,6.3]
    for i, m in enumerate(months):
        series.append({"date": str(m), "indicator": "SELIC",      "value": selic_vals[i]  if i < len(selic_vals)  else 10.5})
        series.append({"date": str(m), "indicator": "IPCA",       "value": ipca_vals[i]   if i < len(ipca_vals)   else 0.30})
        series.append({"date": str(m), "indicator": "DESEMPREGO", "value": desemp_vals[i] if i < len(desemp_vals) else 6.5})

    correlation = [
        {"indicator": "SELIC",      "pearson_r":  0.41, "pearson_p": 0.003,  "pearson_ci_lower": 0.14, "pearson_ci_upper": 0.63, "spearman_rho":  0.38, "spearman_p": 0.007,  "n_obs": 36},
        {"indicator": "IPCA",       "pearson_r":  0.19, "pearson_p": 0.189,  "pearson_ci_lower":-0.10, "pearson_ci_upper": 0.45, "spearman_rho":  0.22, "spearman_p": 0.140,  "n_obs": 36},
        {"indicator": "DESEMPREGO", "pearson_r":  0.63, "pearson_p": 0.0001, "pearson_ci_lower": 0.40, "pearson_ci_upper": 0.79, "spearman_rho":  0.61, "spearman_p": 0.0001, "n_obs": 36},
    ]
    ols = {
        "coefficients": [
            {"term": "(Intercept)", "estimate": 12.450, "std_error": 3.21, "t_statistic": 3.88, "p_value": 0.001},
            {"term": "SELIC",       "estimate":  0.312, "std_error": 0.08, "t_statistic": 3.90, "p_value": 0.001},
            {"term": "IPCA",        "estimate":  0.071, "std_error": 0.12, "t_statistic": 0.59, "p_value": 0.563},
            {"term": "DESEMPREGO",  "estimate":  1.842, "std_error": 0.29, "t_statistic": 6.35, "p_value": 0.0001},
        ],
        "glance": [{"r.squared": 0.4712, "adj.r.squared": 0.4073, "nobs": 36}],
        "residuals": [],
    }
    granger = [
        {"lag": 1, "f_statistic": 4.21, "p_value": 0.041, "conclusion": "Granger-causa a 5%"},
        {"lag": 2, "f_statistic": 5.87, "p_value": 0.009, "conclusion": "Granger-causa a 1%"},
        {"lag": 3, "f_statistic": 4.03, "p_value": 0.048, "conclusion": "Granger-causa a 5%"},
        {"lag": 4, "f_statistic": 3.11, "p_value": 0.081, "conclusion": "Não significativo"},
    ]
    return {"scope": "brasil", "series": series, "correlation": correlation,
            "ols": ols, "granger": granger}


# ---------------------------------------------------------------------------
# Demo mode banner — shown once per session
# ---------------------------------------------------------------------------

def _show_demo_banner() -> None:
    if not st.session_state.get("_demo_banner_shown"):
        st.info(
            "**Modo demo** — dados sintéticos gerados localmente. "
            "Suba o stack com `docker compose up --build` para dados reais.",
            icon="ℹ️",
        )
        st.session_state["_demo_banner_shown"] = True


# ---------------------------------------------------------------------------
# Public API — same signature as before; callers don't change
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def fetch_health() -> dict | None:
    data, live = _get("/health")
    if live:
        return data
    if DEMO_MODE:
        return {"status": "ok", "r_service": "demo", "db": "demo"}
    return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_cases(scope: str, start_date: str | None = None,
                end_date: str | None = None, limit: int = 2000) -> dict | None:
    params: dict = {"limit": limit}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    data, live = _get(f"/cases/{scope}", params)
    if live:
        return data
    if DEMO_MODE:
        _show_demo_banner()
        return _demo_cases(scope)
    return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_vaccination(state: str | None = None, start_date: str | None = None,
                      end_date: str | None = None) -> dict | None:
    params: dict = {"limit": 1000}
    if state:
        params["state"] = state
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    data, live = _get("/vaccination", params)
    if live:
        return data
    if DEMO_MODE:
        _show_demo_banner()
        return _demo_vaccination()
    return None


@st.cache_data(ttl=300, show_spinner=False)
def fetch_forecast(scope: str, model: str = "prophet",
                   horizon: int = 30) -> dict | None:
    data, live = _get("/forecast", {"scope": scope, "model": model, "horizon": horizon})
    if live:
        return data
    if DEMO_MODE:
        _show_demo_banner()
        return _demo_forecast(model, horizon)
    return None


@st.cache_data(ttl=600, show_spinner=False)
def fetch_economics(scope: str = "brasil") -> dict | None:
    data, live = _get("/economics", {"scope": scope})
    if live:
        return data
    if DEMO_MODE:
        _show_demo_banner()
        return _demo_economics()
    return None
