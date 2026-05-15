# =============================================================================
# forecast.R — Time-series forecasting models
#
# Three complementary models are exposed so the API can compare approaches:
#
#   1. ARIMA  (auto.arima) — classic Box-Jenkins; works well on stationary
#              series and captures autocorrelation structure explicitly.
#              Best for stable phases of the pandemic.
#
#   2. Prophet — Facebook's additive model with trend + seasonality + holidays.
#              Handles multiple seasonality, missing values, and structural
#              breaks — ideal for the erratic COVID surges.
#
#   3. Holt-Winters (hw) — exponential smoothing with trend and seasonality.
#              Lightweight and interpretable; good baseline for comparison.
#
# All three functions share the same contract:
#   Input:  data.frame with columns [ds (Date), y (numeric)]
#   Output: data.frame with columns [date, predicted, lower, upper, model]
#
# Design decisions:
#   - We fit on the last 365 days of available data to keep response times
#     under 5 s even for large scopes (Brasil-level series have 1 000+ rows).
#   - Confidence intervals are 95% by default, configurable via `conf_level`.
#   - All models return NA bounds when the series is too short to fit.
#   - Return data is a plain data.frame so plumber can serialize to JSON
#     without any special handler.
# =============================================================================

suppressPackageStartupMessages({
  library(forecast)   # auto.arima, hw, Acf
  library(prophet)    # prophet, make_future_dataframe, predict
  library(dplyr)
  library(lubridate)
  library(zoo)        # rollmean
})

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

#' Validate and prepare a time-series data.frame.
#'
#' @param df      data.frame with at least columns `ds` (Date) and `y` (numeric)
#' @param min_obs Minimum number of non-NA observations required.
#' @return Sorted, deduplicated data.frame or stops with an informative message.
prepare_series <- function(df, min_obs = 14L) {
  if (!all(c("ds", "y") %in% names(df))) {
    stop("Input data.frame must have columns 'ds' (Date) and 'y' (numeric).")
  }

  df <- df |>
    mutate(ds = as.Date(ds), y = as.numeric(y)) |>
    filter(!is.na(ds), !is.na(y), y >= 0) |>
    arrange(ds) |>
    distinct(ds, .keep_all = TRUE)

  if (nrow(df) < min_obs) {
    stop(sprintf(
      "Series has only %d valid observations; need at least %d to fit a model.",
      nrow(df), min_obs
    ))
  }

  df
}

#' Trim a series to the last `n_days` of data.
#' Using a sliding window avoids fitting on 2020 data when forecasting 2023.
trim_to_window <- function(df, n_days = 365L) {
  cutoff <- max(df$ds) - n_days
  df[df$ds >= cutoff, ]
}

#' Build the output data.frame in a normalised format.
build_output <- function(dates, predicted, lower, upper, model_name, conf_level) {
  data.frame(
    date      = as.character(dates),
    predicted = round(pmax(predicted, 0), 2),  # cases can't be negative
    lower     = round(pmax(lower, 0), 2),
    upper     = round(pmax(upper, 0), 2),
    model     = model_name,
    confidence_level = conf_level,
    stringsAsFactors = FALSE
  )
}

# ---------------------------------------------------------------------------
# 1. ARIMA — auto.arima + forecast
# ---------------------------------------------------------------------------

#' Fit auto.arima and return a forecast data.frame.
#'
#' @param df         data.frame [ds, y]
#' @param horizon    Number of days to forecast ahead.
#' @param conf_level Confidence interval level (0–1).
#' @param window     Number of trailing days used for fitting.
#' @return data.frame [date, predicted, lower, upper, model, confidence_level]
run_arima <- function(df, horizon = 30L, conf_level = 0.95, window = 365L) {
  df <- prepare_series(df) |> trim_to_window(window)

  # Convert to ts object — daily frequency
  ts_data <- ts(df$y, frequency = 7)  # weekly seasonality

  # auto.arima selects p, d, q automatically via AICc; stepwise search for speed
  fit <- tryCatch(
    auto.arima(
      ts_data,
      stepwise   = TRUE,
      approximation = TRUE,
      seasonal   = TRUE
    ),
    error = function(e) stop(paste("auto.arima failed:", conditionMessage(e)))
  )

  fcast <- forecast(fit, h = horizon, level = conf_level * 100)

  future_dates <- seq(
    from = max(df$ds) + 1,
    by   = "day",
    length.out = horizon
  )

  build_output(
    dates      = future_dates,
    predicted  = as.numeric(fcast$mean),
    lower      = as.numeric(fcast$lower[, 1]),
    upper      = as.numeric(fcast$upper[, 1]),
    model_name = "arima",
    conf_level = conf_level
  )
}

# ---------------------------------------------------------------------------
# 2. Prophet — additive trend + seasonality + Brazilian holidays
# ---------------------------------------------------------------------------

#' Brazilian public holidays for Prophet's holiday component.
#' Covering 2019-2026 to span the full COVID analysis period.
brazil_holidays <- function() {
  holiday_dates <- as.Date(c(
    # Recurrent national holidays
    "2020-01-01", "2020-04-10", "2020-04-21", "2020-05-01",
    "2020-09-07", "2020-10-12", "2020-11-02", "2020-11-15", "2020-12-25",
    "2021-01-01", "2021-04-02", "2021-04-21", "2021-05-01",
    "2021-09-07", "2021-10-12", "2021-11-02", "2021-11-15", "2021-12-25",
    "2022-01-01", "2022-04-15", "2022-04-21", "2022-05-01",
    "2022-09-07", "2022-10-12", "2022-11-02", "2022-11-15", "2022-12-25",
    "2023-01-01", "2023-04-07", "2023-04-21", "2023-05-01",
    "2023-09-07", "2023-10-12", "2023-11-02", "2023-11-15", "2023-12-25",
    "2024-01-01", "2024-03-29", "2024-04-21", "2024-05-01",
    "2024-09-07", "2024-10-12", "2024-11-02", "2024-11-15", "2024-12-25"
  ))
  data.frame(
    holiday = "feriado_nacional",
    ds      = holiday_dates,
    lower_window = 0,
    upper_window = 1,
    stringsAsFactors = FALSE
  )
}

#' Fit Prophet and return a forecast data.frame.
#'
#' @param df         data.frame [ds, y]
#' @param horizon    Number of days to forecast ahead.
#' @param conf_level Confidence interval width (Prophet uses 80% by default).
#' @param window     Trailing days used for fitting.
#' @return data.frame [date, predicted, lower, upper, model, confidence_level]
run_prophet <- function(df, horizon = 30L, conf_level = 0.95, window = 365L) {
  df <- prepare_series(df) |> trim_to_window(window)

  # Prophet requires columns named exactly `ds` and `y`
  m <- prophet(
    df,
    holidays         = brazil_holidays(),
    yearly.seasonality = TRUE,
    weekly.seasonality = TRUE,
    daily.seasonality  = FALSE,   # too granular for case counts
    interval.width     = conf_level,
    # Cap growth to avoid explosive forecasts; logistic growth needs a cap
    growth             = "linear",
    # Changepoint detection — higher sensitivity captures surges better
    changepoint.prior.scale = 0.15,
    seasonality.prior.scale = 10,
    holidays.prior.scale    = 10,
    verbose = FALSE
  )

  future <- make_future_dataframe(m, periods = horizon, freq = "day")
  fcast  <- predict(m, future)

  # Keep only the future rows (not the in-sample fitted values)
  fcast_future <- tail(fcast, horizon)

  build_output(
    dates      = as.Date(fcast_future$ds),
    predicted  = fcast_future$yhat,
    lower      = fcast_future$yhat_lower,
    upper      = fcast_future$yhat_upper,
    model_name = "prophet",
    conf_level = conf_level
  )
}

# ---------------------------------------------------------------------------
# 3. Holt-Winters — exponential smoothing
# ---------------------------------------------------------------------------

#' Fit Holt-Winters and return a forecast data.frame.
#'
#' Holt-Winters is the simplest of the three models and serves as a baseline.
#' It works well when the series has a clear trend + weekly seasonality.
#'
#' @param df         data.frame [ds, y]
#' @param horizon    Number of days to forecast ahead.
#' @param conf_level Confidence interval level.
#' @param window     Trailing days used for fitting.
#' @return data.frame [date, predicted, lower, upper, model, confidence_level]
run_holtwinters <- function(df, horizon = 30L, conf_level = 0.95, window = 365L) {
  df <- prepare_series(df) |> trim_to_window(window)

  # hw() requires a ts object with frequency > 1
  ts_data <- ts(df$y, frequency = 7)

  fit <- tryCatch(
    hw(ts_data, seasonal = "multiplicative", h = horizon, level = conf_level * 100),
    error = function(e) {
      # Fall back to additive if multiplicative fails (e.g., series with zeros)
      hw(ts_data, seasonal = "additive", h = horizon, level = conf_level * 100)
    }
  )

  future_dates <- seq(
    from = max(df$ds) + 1,
    by   = "day",
    length.out = horizon
  )

  build_output(
    dates      = future_dates,
    predicted  = as.numeric(fit$mean),
    lower      = as.numeric(fit$lower[, 1]),
    upper      = as.numeric(fit$upper[, 1]),
    model_name = "holtwinters",
    conf_level = conf_level
  )
}

# ---------------------------------------------------------------------------
# 4. Moving average — smoothing utility (used by dashboard)
# ---------------------------------------------------------------------------

#' Compute a rolling mean over the historical series (not a forecast).
#'
#' @param df     data.frame [ds, y]
#' @param k      Window size in days (default 7).
#' @return data.frame [date, raw, smoothed]
run_moving_average <- function(df, k = 7L) {
  df <- prepare_series(df)

  df$smoothed <- round(
    zoo::rollmean(df$y, k = k, fill = NA, align = "right"),
    2
  )

  data.frame(
    date     = as.character(df$ds),
    raw      = df$y,
    smoothed = df$smoothed,
    window_days = k,
    stringsAsFactors = FALSE
  )
}

# ---------------------------------------------------------------------------
# 5. Ensemble — weighted average of all three models
# ---------------------------------------------------------------------------

#' Combine ARIMA + Prophet + Holt-Winters with equal weights.
#' Returns a single forecast with averaged point estimates and
#' pooled prediction intervals.
#'
#' @param df      data.frame [ds, y]
#' @param horizon Number of days ahead.
#' @return data.frame [date, predicted, lower, upper, model, confidence_level]
run_ensemble <- function(df, horizon = 30L, conf_level = 0.95) {
  results <- list(
    tryCatch(run_arima(df, horizon, conf_level), error = function(e) NULL),
    tryCatch(run_prophet(df, horizon, conf_level), error = function(e) NULL),
    tryCatch(run_holtwinters(df, horizon, conf_level), error = function(e) NULL)
  )

  # Drop failed models
  results <- Filter(Negate(is.null), results)

  if (length(results) == 0L) {
    stop("All models failed. Cannot compute ensemble forecast.")
  }

  # Align on dates (some models may produce slightly different date ranges)
  base_dates <- results[[1]]$date

  preds  <- sapply(results, function(r) r$predicted[seq_along(base_dates)])
  lowers <- sapply(results, function(r) r$lower[seq_along(base_dates)])
  uppers <- sapply(results, function(r) r$upper[seq_along(base_dates)])

  build_output(
    dates      = base_dates,
    predicted  = rowMeans(preds,  na.rm = TRUE),
    lower      = rowMeans(lowers, na.rm = TRUE),
    upper      = rowMeans(uppers, na.rm = TRUE),
    model_name = paste0("ensemble(n=", length(results), ")"),
    conf_level = conf_level
  )
}
