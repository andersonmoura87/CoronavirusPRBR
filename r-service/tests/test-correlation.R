# =============================================================================
# test-correlation.R — testthat coverage for models/correlation.R
# =============================================================================

library(testthat)

# Source models only when not already loaded (e.g. by the CI inline script).
if (!exists("run_correlation", mode = "function")) {
  model_file <- tryCatch(
    file.path(dirname(sys.frame(1)$ofile), "..", "models", "correlation.R"),
    error = function(e) "r-service/models/correlation.R"
  )
  source(model_file, local = FALSE)
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
make_covid_df <- function(n_months = 24) {
  dates <- seq(as.Date("2020-03-01"), by = "month", length.out = n_months)
  data.frame(
    date  = as.character(dates),
    cases = round(abs(rnorm(n_months, mean = 5000, sd = 1500))),
    stringsAsFactors = FALSE
  )
}

make_eco_df <- function(n_months = 24) {
  dates <- seq(as.Date("2020-03-01"), by = "month", length.out = n_months)
  rbind(
    data.frame(date = as.character(dates),
               indicator = "SELIC",
               value = round(seq(2, 13, length.out = n_months), 2),
               stringsAsFactors = FALSE),
    data.frame(date = as.character(dates),
               indicator = "IPCA",
               value = round(rnorm(n_months, 0.4, 0.2), 4),
               stringsAsFactors = FALSE),
    data.frame(date = as.character(dates),
               indicator = "DESEMPREGO",
               value = round(seq(14, 9, length.out = n_months), 2),
               stringsAsFactors = FALSE)
  )
}

# ---------------------------------------------------------------------------
# aggregate_covid_monthly
# ---------------------------------------------------------------------------
test_that("aggregate_covid_monthly sums daily values to months", {
  daily <- data.frame(
    date  = c("2021-01-05", "2021-01-20", "2021-02-10"),
    cases = c(100L, 200L, 150L),
    stringsAsFactors = FALSE
  )
  result <- aggregate_covid_monthly(daily)
  expect_equal(nrow(result), 2L)
  jan <- result[result$month == as.Date("2021-01-01"), ]
  expect_equal(jan$cases_monthly, 300L)
})

test_that("aggregate_covid_monthly drops negative case values", {
  df <- data.frame(
    date  = c("2021-01-05", "2021-01-06"),
    cases = c(-10L, 100L),
    stringsAsFactors = FALSE
  )
  result <- aggregate_covid_monthly(df)
  expect_equal(result$cases_monthly, 100L)
})

# ---------------------------------------------------------------------------
# pivot_economics_wide
# ---------------------------------------------------------------------------
test_that("pivot_economics_wide creates one column per indicator", {
  eco  <- make_eco_df(6)
  wide <- pivot_economics_wide(eco)
  expect_true("SELIC"     %in% names(wide))
  expect_true("IPCA"      %in% names(wide))
  expect_true("DESEMPREGO" %in% names(wide))
})

# ---------------------------------------------------------------------------
# build_analysis_df
# ---------------------------------------------------------------------------
test_that("build_analysis_df returns only complete cases", {
  covid <- make_covid_df(12)
  eco   <- make_eco_df(12)
  df    <- build_analysis_df(
    aggregate_covid_monthly(covid),
    pivot_economics_wide(eco)
  )
  expect_true(nrow(df) > 0)
  expect_true(all(complete.cases(df)))
})

# ---------------------------------------------------------------------------
# compute_pairwise_correlation
# ---------------------------------------------------------------------------
test_that("compute_pairwise_correlation returns one row per indicator", {
  covid <- make_covid_df(24)
  eco   <- make_eco_df(24)
  df    <- build_analysis_df(
    aggregate_covid_monthly(covid),
    pivot_economics_wide(eco)
  )
  result <- compute_pairwise_correlation(df)
  expect_equal(nrow(result), 3L)   # SELIC, IPCA, DESEMPREGO
  expect_named(result, c("indicator", "pearson_r", "pearson_p",
                         "pearson_ci_lower", "pearson_ci_upper",
                         "spearman_rho", "spearman_p", "n_obs"),
               ignore.order = TRUE)
})

test_that("compute_pairwise_correlation p-values are in [0, 1]", {
  set.seed(1)
  covid <- make_covid_df(30)
  eco   <- make_eco_df(30)
  df    <- build_analysis_df(aggregate_covid_monthly(covid), pivot_economics_wide(eco))
  result <- compute_pairwise_correlation(df)
  valid_p <- result$pearson_p[!is.na(result$pearson_p)]
  expect_true(all(valid_p >= 0 & valid_p <= 1))
})

# ---------------------------------------------------------------------------
# run_ols_regression
# ---------------------------------------------------------------------------
test_that("run_ols_regression returns a list with coefficients and glance", {
  set.seed(2)
  covid <- make_covid_df(30)
  eco   <- make_eco_df(30)
  df    <- build_analysis_df(aggregate_covid_monthly(covid), pivot_economics_wide(eco))
  result <- run_ols_regression(df)
  expect_type(result, "list")
  expect_true("coefficients" %in% names(result))
  expect_true("glance"       %in% names(result))
  expect_true("residuals"    %in% names(result))
})

test_that("run_ols_regression glance has r.squared between 0 and 1", {
  set.seed(3)
  covid <- make_covid_df(30)
  eco   <- make_eco_df(30)
  df    <- build_analysis_df(aggregate_covid_monthly(covid), pivot_economics_wide(eco))
  glance <- run_ols_regression(df)$glance
  r2 <- as.numeric(glance[1, grep("r.squared|r_squared", names(glance))[1]])
  expect_gte(r2, 0)
  expect_lte(r2, 1)
})

# ---------------------------------------------------------------------------
# run_lagged_correlation
# ---------------------------------------------------------------------------
test_that("run_lagged_correlation returns 2*max_lag+1 rows", {
  set.seed(4)
  covid <- make_covid_df(30)
  eco   <- make_eco_df(30)
  df    <- build_analysis_df(aggregate_covid_monthly(covid), pivot_economics_wide(eco))
  result <- run_lagged_correlation(df, indicator = "SELIC", max_lag = 3L)
  expect_equal(nrow(result), 7L)  # lags -3 to +3
  expect_equal(sort(result$lag_months), -3:3)
})

test_that("run_lagged_correlation errors on unknown indicator", {
  df <- data.frame(month = as.Date("2021-01-01"), cases_monthly = 100,
                   SELIC = 5.0)
  expect_error(run_lagged_correlation(df, "UNKNOWN"), "not found")
})
