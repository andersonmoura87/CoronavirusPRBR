# Suppress R CMD CHECK / lintr false positives for dplyr NSE column references.
# These variables (cases, value, indicator, ds, y) are used inside dplyr verbs
# such as filter(), mutate(), and summarise() via non-standard evaluation (NSE).
utils::globalVariables(c("cases", "value", "indicator", "month"))

# =============================================================================
# correlation.R — COVID-19 × macroeconomic indicator correlation analysis
#
# This module quantifies the relationship between COVID-19 case burden and
# key macroeconomic series (Selic, IPCA, unemployment) using:
#
#   1. Pearson and Spearman correlation coefficients with p-values
#   2. OLS regression: cases ~ selic + ipca + desemprego  (lm)
#   3. Lagged correlation analysis — the economic impact of a COVID surge
#      is typically felt 1–3 months later, so we cross-correlate at lags
#      0 through 6 months.
#   4. Granger causality test — does COVID Granger-cause unemployment?
#
# All functions return plain data.frames for JSON serialization via plumber.
#
# Input contract:
#   covid_df    — data.frame [date (Date/character), cases (numeric)]
#   economic_df — data.frame [date (Date/character), indicator (character),
#                              value (numeric)]
#
# Design decision: we work at monthly granularity because economic indicators
# (Selic, IPCA, PNAD) are published monthly, not daily. Daily COVID cases are
# summed to monthly totals before joining.
# =============================================================================

suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
  library(lubridate)
  library(broom)      # tidy() — converts lm() output to tidy data.frames
  library(lmtest)     # grangertest()
  library(zoo)        # rollmean for pre-processing
})

# ---------------------------------------------------------------------------
# Data preparation
# ---------------------------------------------------------------------------

#' Aggregate daily COVID cases to monthly totals and normalise the date column.
#'
#' @param covid_df data.frame with [date, cases] columns.
#' @return Monthly data.frame [month (Date, first of month), cases_monthly].
aggregate_covid_monthly <- function(covid_df) {
  covid_df |>
    mutate(
      date   = as.Date(date),
      month  = floor_date(date, "month"),
      cases  = as.numeric(cases)
    ) |>
    filter(!is.na(cases), cases >= 0) |>
    group_by(month) |>
    summarise(cases_monthly = sum(cases, na.rm = TRUE), .groups = "drop") |>
    arrange(month)
}

#' Pivot economic indicators from long to wide format and align to monthly dates.
#'
#' @param economic_df data.frame [date, indicator, value].
#' @return Wide data.frame [month, SELIC, IPCA, DESEMPREGO, ...].
pivot_economics_wide <- function(economic_df) {
  economic_df |>
    mutate(
      month = floor_date(as.Date(date), "month"),
      value = as.numeric(value)
    ) |>
    filter(!is.na(value)) |>
    select(month, indicator, value) |>
    pivot_wider(names_from = indicator, values_from = value, values_fn = mean) |>
    arrange(month)
}

#' Join COVID monthly totals with economic indicators.
#' Inner join ensures we only analyse periods where both series are available.
#'
#' @param covid_df    Output of aggregate_covid_monthly().
#' @param economic_df Output of pivot_economics_wide().
#' @return Joined data.frame, complete cases only.
build_analysis_df <- function(covid_df, economic_df) {
  df <- inner_join(covid_df, economic_df, by = "month")
  df[complete.cases(df), ]
}

# ---------------------------------------------------------------------------
# 1. Pairwise correlation
# ---------------------------------------------------------------------------

#' Compute Pearson and Spearman correlation between COVID cases and each
#' economic indicator, with p-values and confidence intervals.
#'
#' @param df  Output of build_analysis_df().
#' @return data.frame [indicator, pearson_r, pearson_p, spearman_rho, spearman_p].
compute_pairwise_correlation <- function(df) {
  # Economic indicator columns (everything except 'month' and 'cases_monthly')
  indicators <- setdiff(names(df), c("month", "cases_monthly"))

  if (length(indicators) == 0L) {
    stop("No economic indicator columns found in the joined data.frame.")
  }

  results <- lapply(indicators, function(ind) {
    x <- df$cases_monthly
    y <- df[[ind]]

    # Drop pairs with NA
    valid <- complete.cases(x, y)
    x <- x[valid]
    y <- y[valid]

    if (length(x) < 5L) {
      return(data.frame(
        indicator   = ind,
        pearson_r   = NA_real_,
        pearson_p   = NA_real_,
        spearman_rho = NA_real_,
        spearman_p  = NA_real_,
        n_obs       = length(x),
        stringsAsFactors = FALSE
      ))
    }

    pearson  <- cor.test(x, y, method = "pearson",  conf.level = 0.95)
    spearman <- cor.test(x, y, method = "spearman", exact = FALSE)

    data.frame(
      indicator    = ind,
      pearson_r    = round(pearson$estimate, 4),
      pearson_p    = round(pearson$p.value, 6),
      pearson_ci_lower = round(pearson$conf.int[1], 4),
      pearson_ci_upper = round(pearson$conf.int[2], 4),
      spearman_rho = round(spearman$estimate, 4),
      spearman_p   = round(spearman$p.value, 6),
      n_obs        = length(x),
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, results)
}

# ---------------------------------------------------------------------------
# 2. OLS multiple regression — cases ~ indicators
# ---------------------------------------------------------------------------

#' Fit an OLS model: monthly_cases ~ selic + ipca + unemployment.
#'
#' Returns three tidy data.frames:
#'   $coefficients — one row per predictor (estimate, std.error, t, p)
#'   $glance       — model-level stats (R², adj-R², F, p, AIC)
#'   $residuals    — residual series for diagnostics
#'
#' @param df Output of build_analysis_df().
#' @param predictors Character vector of column names to use as predictors.
#'                   Defaults to all economic indicator columns.
#' @return List with elements: coefficients, glance, residuals.
run_ols_regression <- function(df, predictors = NULL) {
  if (is.null(predictors)) {
    predictors <- setdiff(names(df), c("month", "cases_monthly"))
  }

  # Keep only predictors that exist in the data
  predictors <- intersect(predictors, names(df))
  if (length(predictors) == 0L) {
    stop("No valid predictor columns found.")
  }

  formula_str <- paste("cases_monthly ~", paste(predictors, collapse = " + "))
  fit <- lm(as.formula(formula_str), data = df)

  list(
    formula      = formula_str,
    coefficients = tidy(fit, conf.int = TRUE) |>
      mutate(across(where(is.numeric), \(x) round(x, 6))),
    glance       = glance(fit) |>
      mutate(across(where(is.numeric), \(x) round(x, 6))),
    residuals    = data.frame(
      month    = as.character(df$month),
      residual = round(residuals(fit), 2),
      fitted   = round(fitted(fit), 2),
      stringsAsFactors = FALSE
    )
  )
}

# ---------------------------------------------------------------------------
# 3. Lagged cross-correlation
# ---------------------------------------------------------------------------

#' Cross-correlate COVID cases with an economic indicator at lags 0–max_lag months.
#'
#' A positive lag means the economic indicator FOLLOWS the COVID surge (delayed
#' impact); a negative lag means it PRECEDES it (predictive signal).
#'
#' @param df      Output of build_analysis_df().
#' @param indicator Column name of the economic indicator.
#' @param max_lag   Maximum lag in months to test.
#' @return data.frame [lag_months, correlation, p_value].
run_lagged_correlation <- function(df, indicator, max_lag = 6L) {
  if (!indicator %in% names(df)) {
    stop(sprintf("Indicator '%s' not found in data.frame.", indicator))
  }

  lags <- seq(-max_lag, max_lag)

  results <- lapply(lags, function(lag) {
    cases_shifted <- df$cases_monthly
    eco_shifted   <- df[[indicator]]

    n <- nrow(df)
    if (lag > 0) {
      # Positive lag: economic indicator is lagged behind COVID
      cases_shifted <- cases_shifted[1:(n - lag)]
      eco_shifted   <- eco_shifted[(lag + 1):n]
    } else if (lag < 0) {
      # Negative lag: economic indicator leads COVID
      l <- abs(lag)
      cases_shifted <- cases_shifted[(l + 1):n]
      eco_shifted   <- eco_shifted[1:(n - l)]
    }

    valid <- complete.cases(cases_shifted, eco_shifted)
    x <- cases_shifted[valid]
    y <- eco_shifted[valid]

    if (length(x) < 5L) {
      return(data.frame(
        lag_months  = lag,
        correlation = NA_real_,
        p_value     = NA_real_,
        stringsAsFactors = FALSE
      ))
    }

    ct <- cor.test(x, y, method = "pearson")
    data.frame(
      lag_months  = lag,
      correlation = round(ct$estimate, 4),
      p_value     = round(ct$p.value, 6),
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, results)
}

# ---------------------------------------------------------------------------
# 4. Granger causality — does COVID Granger-cause unemployment?
# ---------------------------------------------------------------------------

#' Test whether lagged COVID cases help predict unemployment (Granger causality).
#'
#' This is a frequentist test — it doesn't prove causation, but it supports
#' the narrative that COVID spikes preceded unemployment spikes.
#'
#' @param df    Output of build_analysis_df().
#' @param order Maximum lag order (in months) to test.
#' @return data.frame [lag, f_statistic, p_value, conclusion].
run_granger_test <- function(df, order = 3L) {
  if (!"DESEMPREGO" %in% names(df)) {
    stop("Column 'DESEMPREGO' (unemployment) not found. Cannot run Granger test.")
  }

  granger_df <- df[, c("cases_monthly", "DESEMPREGO")]
  granger_df <- granger_df[complete.cases(granger_df), ]

  if (nrow(granger_df) < order * 3L) {
    stop(sprintf(
      "Too few observations (%d) for Granger test with order %d.",
      nrow(granger_df), order
    ))
  }

  results <- lapply(seq_len(order), function(k) {
    gt <- tryCatch(
      grangertest(DESEMPREGO ~ cases_monthly, order = k, data = granger_df),
      error = function(e) NULL
    )

    if (is.null(gt)) {
      return(data.frame(
        lag         = k,
        f_statistic = NA_real_,
        p_value     = NA_real_,
        conclusion  = "test failed",
        stringsAsFactors = FALSE
      ))
    }

    p <- gt$`Pr(>F)`[2]
    data.frame(
      lag         = k,
      f_statistic = round(gt$F[2], 4),
      p_value     = round(p, 6),
      conclusion  = ifelse(
        !is.na(p) & p < 0.05,
        "COVID Granger-causes unemployment (p < 0.05)",
        "No Granger causality detected"
      ),
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, results)
}

# ---------------------------------------------------------------------------
# 5. Full analysis — convenience wrapper used by the plumber endpoint
# ---------------------------------------------------------------------------

#' Run the complete COVID × economics correlation analysis.
#'
#' @param covid_df    data.frame [date, cases]
#' @param economic_df data.frame [date, indicator, value]
#' @return Named list suitable for JSON serialization.
run_full_correlation <- function(covid_df, economic_df) {
  covid_monthly <- aggregate_covid_monthly(covid_df)
  eco_wide      <- pivot_economics_wide(economic_df)
  df            <- build_analysis_df(covid_monthly, eco_wide)

  indicators    <- setdiff(names(df), c("month", "cases_monthly"))

  list(
    n_months          = nrow(df),
    date_range        = list(
      start = as.character(min(df$month)),
      end   = as.character(max(df$month))
    ),
    pairwise_correlation = compute_pairwise_correlation(df),
    ols_regression       = run_ols_regression(df),
    lagged_correlation   = lapply(
      indicators,
      function(ind) {
        list(
          indicator = ind,
          lags      = run_lagged_correlation(df, ind, max_lag = 6L)
        )
      }
    ),
    granger_causality  = tryCatch(
      run_granger_test(df, order = 3L),
      error = function(e) list(error = conditionMessage(e))
    )
  )
}
