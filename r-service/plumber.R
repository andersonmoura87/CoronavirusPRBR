# =============================================================================
# plumber.R — R microservice REST API
#
# This file is the entry point for the R plumber microservice.
# FastAPI calls these endpoints internally (service-to-service) to run
# statistical models in R without exposing them directly to the public internet.
#
# Base URL inside Docker network: http://r-service:8001
#
# Endpoints:
#   POST /forecast        — time-series forecast (ARIMA / Prophet / Holt-Winters)
#   GET  /smooth          — rolling average smoothing
#   POST /correlation     — COVID × economics correlation analysis
#   GET  /health          — liveness probe
#   GET  /model-info      — version metadata for all loaded packages
#
# Design decisions:
#   - All endpoints accept JSON; POST bodies are parsed automatically by plumber.
#   - Error handling uses tryCatch everywhere so the API always returns a
#     structured JSON error instead of a raw R traceback.
#   - CORS is enabled for local Streamlit development (origin = *).
#   - Response serialisation uses plumber's default JSON serializer (jsonlite).
#   - Logging is written to stdout so Docker captures it via its log driver.
# =============================================================================

library(plumber)

# Source model modules — paths are relative to this file's directory
source(file.path(dirname(sys.frame(1)$ofile), "models", "forecast.R"),
       local = TRUE)
source(file.path(dirname(sys.frame(1)$ofile), "models", "correlation.R"),
       local = TRUE)

# ---------------------------------------------------------------------------
# Plumber router definition
# ---------------------------------------------------------------------------

#* @apiTitle Pandemic Data Platform — R Statistical Models
#* @apiDescription Microservice providing epidemiological forecasts and
#*   COVID×economics correlation analysis for the FastAPI layer.
#* @apiVersion 1.0.0

#* Enable CORS for all origins (service-to-service + local dashboard)
#* @filter cors
function(req, res) {
  res$setHeader("Access-Control-Allow-Origin",  "*")
  res$setHeader("Access-Control-Allow-Headers", "Content-Type, Authorization")
  res$setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
  if (req$REQUEST_METHOD == "OPTIONS") {
    res$status <- 200L
    return(list())
  }
  plumber::forward()
}

# ---------------------------------------------------------------------------
# GET /health — Kubernetes liveness + readiness probe
# ---------------------------------------------------------------------------

#* Liveness probe — returns 200 if the service is up.
#* @get /health
#* @serializer json
function() {
  list(
    status    = "ok",
    service   = "r-service",
    timestamp = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
    r_version = R.version.string
  )
}

# ---------------------------------------------------------------------------
# GET /model-info — package versions for reproducibility audit
# ---------------------------------------------------------------------------

#* Return versions of all statistical packages loaded by the service.
#* @get /model-info
#* @serializer json
function() {
  pkgs <- c("forecast", "prophet", "dplyr", "tidyr", "lubridate",
            "broom", "lmtest", "zoo", "plumber")
  versions <- lapply(pkgs, function(p) {
    tryCatch(
      as.character(packageVersion(p)),
      error = function(e) "not installed"
    )
  })
  names(versions) <- pkgs
  list(
    r_version = R.version.string,
    packages  = versions,
    built_at  = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  )
}

# ---------------------------------------------------------------------------
# POST /forecast — time-series forecast
# ---------------------------------------------------------------------------
#
# Request body (JSON):
# {
#   "scope":      "brasil" | "parana" | "maringa",
#   "model":      "arima" | "prophet" | "holtwinters" | "ensemble",
#   "horizon":    30,           // days ahead (default 30, max 90)
#   "conf_level": 0.95,         // confidence interval width
#   "data": [                   // historical series
#     {"ds": "2021-01-01", "y": 1234},
#     ...
#   ]
# }
#
# Response body (JSON):
# {
#   "scope": "brasil",
#   "model": "prophet",
#   "horizon": 30,
#   "forecast": [
#     {"date": "2023-05-01", "predicted": 8200.5, "lower": 6100.0, "upper": 10300.0, ...},
#     ...
#   ],
#   "meta": { ... }
# }

#* Run a time-series forecast on COVID case data.
#* @post /forecast
#* @serializer json
function(req) {
  body <- tryCatch(
    jsonlite::fromJSON(req$postBody, simplifyDataFrame = TRUE),
    error = function(e) stop("Invalid JSON body: ", conditionMessage(e))
  )

  # ── Input validation ──────────────────────────────────────────────────────
  scope     <- tolower(body$scope     %||% "brasil")
  model_key <- tolower(body$model     %||% "prophet")
  horizon   <- as.integer(body$horizon   %||% 30L)
  conf_level <- as.numeric(body$conf_level %||% 0.95)
  data_list <- body$data

  if (!model_key %in% c("arima", "prophet", "holtwinters", "ensemble")) {
    stop(sprintf("Unknown model '%s'. Valid: arima, prophet, holtwinters, ensemble.", model_key))
  }
  horizon <- min(max(horizon, 1L), 90L)  # clamp to [1, 90]
  conf_level <- min(max(conf_level, 0.5), 0.99)

  if (is.null(data_list) || nrow(data_list) == 0L) {
    stop("'data' field is required and must contain at least one row.")
  }

  df <- tryCatch(
    as.data.frame(data_list),
    error = function(e) stop("Could not parse 'data': ", conditionMessage(e))
  )

  # ── Dispatch to the correct model ─────────────────────────────────────────
  t_start <- proc.time()[["elapsed"]]

  forecast_df <- tryCatch(
    switch(model_key,
      arima       = run_arima(df, horizon, conf_level),
      prophet     = run_prophet(df, horizon, conf_level),
      holtwinters = run_holtwinters(df, horizon, conf_level),
      ensemble    = run_ensemble(df, horizon, conf_level)
    ),
    error = function(e) {
      stop(sprintf("Model '%s' failed: %s", model_key, conditionMessage(e)))
    }
  )

  elapsed_ms <- round((proc.time()[["elapsed"]] - t_start) * 1000)

  list(
    scope    = scope,
    model    = model_key,
    horizon  = horizon,
    forecast = forecast_df,
    meta = list(
      n_input_rows   = nrow(df),
      elapsed_ms     = elapsed_ms,
      generated_at   = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC"),
      r_version      = R.version.string
    )
  )
}

# ---------------------------------------------------------------------------
# POST /smooth — rolling average on historical data
# ---------------------------------------------------------------------------
#
# Request body (JSON):
# {
#   "data":   [{"ds": "2021-01-01", "y": 1234}, ...],
#   "window": 7
# }

#* Apply rolling average smoothing to a COVID case series.
#* @post /smooth
#* @serializer json
function(req) {
  body <- tryCatch(
    jsonlite::fromJSON(req$postBody, simplifyDataFrame = TRUE),
    error = function(e) stop("Invalid JSON body.")
  )

  df     <- as.data.frame(body$data)
  window <- as.integer(body$window %||% 7L)
  window <- min(max(window, 2L), 30L)

  result <- tryCatch(
    run_moving_average(df, k = window),
    error = function(e) stop("Smoothing failed: ", conditionMessage(e))
  )

  list(
    window_days = window,
    smoothed    = result
  )
}

# ---------------------------------------------------------------------------
# POST /correlation — COVID × economics analysis
# ---------------------------------------------------------------------------
#
# Request body (JSON):
# {
#   "covid": [
#     {"date": "2021-01-01", "cases": 5000},
#     ...
#   ],
#   "economics": [
#     {"date": "2021-01-01", "indicator": "SELIC", "value": 2.0},
#     {"date": "2021-01-01", "indicator": "IPCA",  "value": 0.25},
#     ...
#   ]
# }

#* Run COVID×economics correlation and Granger causality analysis.
#* @post /correlation
#* @serializer json
function(req) {
  body <- tryCatch(
    jsonlite::fromJSON(req$postBody, simplifyDataFrame = TRUE),
    error = function(e) stop("Invalid JSON body.")
  )

  covid_df    <- as.data.frame(body$covid)
  economic_df <- as.data.frame(body$economics)

  if (nrow(covid_df) == 0L || nrow(economic_df) == 0L) {
    stop("Both 'covid' and 'economics' arrays must be non-empty.")
  }

  # Rename 'cases' → 'y' so aggregate_covid_monthly works
  if ("cases" %in% names(covid_df) && !"y" %in% names(covid_df)) {
    names(covid_df)[names(covid_df) == "cases"] <- "y"
  }
  if (!"date" %in% names(covid_df)) {
    stop("'covid' array must have a 'date' column.")
  }
  # aggregate_covid_monthly expects 'date' and 'cases'
  names(covid_df)[names(covid_df) == "y"] <- "cases"

  t_start <- proc.time()[["elapsed"]]

  result <- tryCatch(
    run_full_correlation(covid_df, economic_df),
    error = function(e) stop("Correlation analysis failed: ", conditionMessage(e))
  )

  result$meta <- list(
    elapsed_ms   = round((proc.time()[["elapsed"]] - t_start) * 1000),
    generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
  )

  result
}

# ---------------------------------------------------------------------------
# Null-coalescing operator (base R doesn't have %||% without rlang)
# ---------------------------------------------------------------------------

`%||%` <- function(lhs, rhs) if (!is.null(lhs)) lhs else rhs
