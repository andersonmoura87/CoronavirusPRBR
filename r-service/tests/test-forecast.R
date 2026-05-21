# =============================================================================
# test-forecast.R — testthat coverage for models/forecast.R
# =============================================================================

library(testthat)

# test_dir() uses withr::with_dir() so CWD is r-service/tests, not repo root.
# test_path() returns an absolute path anchored to the test directory — it is
# the only reliable way to source sibling directories in all invocation modes.
# Fall back to "../models/X.R" (relative to tests/) when test_path() is not
# available (e.g. interactive run outside test_dir()).
if (!exists("run_ensemble", mode = "function")) {
  model_file <- tryCatch(
    testthat::test_path("..", "models", "forecast.R"),
    error = function(e) "../models/forecast.R"
  )
  source(model_file, local = FALSE)
}

# ---------------------------------------------------------------------------
# Helper: generate a clean daily series (30 or 60 days)
# ---------------------------------------------------------------------------
make_series <- function(n = 60, seed = 42) {
  set.seed(seed)
  dates <- seq(as.Date("2021-01-01"), by = "day", length.out = n)
  data.frame(
    ds = dates,
    y  = pmax(0, round(rnorm(n, mean = 500, sd = 80))),
    stringsAsFactors = FALSE
  )
}

# ---------------------------------------------------------------------------
# prepare_series
# ---------------------------------------------------------------------------
test_that("prepare_series rejects missing columns", {
  expect_error(prepare_series(data.frame(x = 1:5)), "columns 'ds'")
})

test_that("prepare_series drops NA rows and sorts by date", {
  df <- make_series(20)
  df$y[c(3, 7)] <- NA
  result <- prepare_series(df)
  expect_equal(nrow(result), 18L)
  expect_true(all(diff(as.numeric(result$ds)) > 0))  # sorted ascending
})

test_that("prepare_series drops negative values", {
  df <- make_series(20)
  df$y[1] <- -10
  result <- prepare_series(df)
  expect_equal(nrow(result), 19L)
})

test_that("prepare_series errors on too few observations", {
  df <- make_series(5)
  expect_error(prepare_series(df, min_obs = 14), "at least 14")
})

# ---------------------------------------------------------------------------
# trim_to_window
# ---------------------------------------------------------------------------
test_that("trim_to_window keeps only last n_days", {
  df <- make_series(100)
  trimmed <- trim_to_window(df, n_days = 30L)
  expect_lte(nrow(trimmed), 31L)
  expect_equal(max(trimmed$ds), max(df$ds))
})

# ---------------------------------------------------------------------------
# build_output
# ---------------------------------------------------------------------------
test_that("build_output returns correct column names", {
  result <- build_output(
    dates      = as.Date("2021-01-01") + 0:4,
    predicted  = c(10, 20, 30, 40, 50),
    lower      = c(5,  15, 25, 35, 45),
    upper      = c(15, 25, 35, 45, 55),
    model_name = "test",
    conf_level = 0.95
  )
  expect_named(result, c("date", "predicted", "lower", "upper",
                         "model", "confidence_level"))
})

test_that("build_output clamps negative predicted values to 0", {
  result <- build_output(
    dates      = as.Date("2021-01-01"),
    predicted  = -50,
    lower      = -100,
    upper      = -10,
    model_name = "test",
    conf_level = 0.95
  )
  expect_equal(result$predicted, 0)
  expect_equal(result$lower, 0)
})

# ---------------------------------------------------------------------------
# run_moving_average
# ---------------------------------------------------------------------------
test_that("run_moving_average returns expected columns", {
  df     <- make_series(30)
  result <- run_moving_average(df, k = 7L)
  expect_named(result, c("date", "raw", "smoothed", "window_days"))
  expect_equal(nrow(result), 30L)
  expect_equal(result$window_days[1], 7L)
})

test_that("run_moving_average first (k-1) smoothed values are NA", {
  df     <- make_series(14)
  result <- run_moving_average(df, k = 7L)
  # rollmean with fill = NA produces NA for the first k-1 rows
  expect_true(sum(is.na(result$smoothed)) >= 6L)
})

# ---------------------------------------------------------------------------
# run_holtwinters — fastest model, safe to run in tests
# ---------------------------------------------------------------------------
test_that("run_holtwinters returns a data.frame with correct shape", {
  df     <- make_series(60)
  result <- run_holtwinters(df, horizon = 14L, conf_level = 0.95)
  expect_s3_class(result, "data.frame")
  expect_equal(nrow(result), 14L)
  expect_named(result, c("date", "predicted", "lower", "upper",
                         "model", "confidence_level"))
})

test_that("run_holtwinters upper >= predicted >= lower", {
  df     <- make_series(60)
  result <- run_holtwinters(df, horizon = 7L)
  expect_true(all(result$upper >= result$predicted))
  expect_true(all(result$predicted >= result$lower))
})

test_that("run_holtwinters forecast dates start the day after the last training date", {
  df          <- make_series(60)
  last_train  <- max(as.Date(df$ds))
  result      <- run_holtwinters(df, horizon = 7L)
  first_fcast <- as.Date(result$date[1])
  expect_equal(first_fcast, last_train + 1L)
})

# ---------------------------------------------------------------------------
# run_arima — skip if too slow in CI (mark with skip_on_cran())
# ---------------------------------------------------------------------------
test_that("run_arima returns correct horizon length", {
  skip_if_not_installed("forecast")
  df     <- make_series(90)
  result <- run_arima(df, horizon = 10L)
  expect_equal(nrow(result), 10L)
  expect_equal(result$model[1], "arima")
})

# ---------------------------------------------------------------------------
# run_ensemble — tests that partial model failure is handled gracefully
#
# with_mocked_bindings() requires pkgload (devtools::load_all()), which is
# not available when files are sourced directly into the global environment.
# We replace functions in .GlobalEnv manually and restore them with on.exit().
# ---------------------------------------------------------------------------
test_that("run_ensemble returns data.frame even if one model fails", {
  skip_if_not_installed("forecast")
  df <- make_series(90)
  orig_prophet <- run_prophet
  assign("run_prophet", function(...) stop("Prophet unavailable"), envir = .GlobalEnv)
  on.exit(assign("run_prophet", orig_prophet, envir = .GlobalEnv), add = TRUE)

  result <- run_ensemble(df, horizon = 7L)
  expect_s3_class(result, "data.frame")
  expect_equal(nrow(result), 7L)
  expect_match(result$model[1], "^ensemble")
})

test_that("run_ensemble errors when ALL models fail", {
  df <- make_series(90)
  orig_arima   <- run_arima
  orig_prophet <- run_prophet
  orig_hw      <- run_holtwinters
  assign("run_arima",       function(...) stop("arima fail"),   envir = .GlobalEnv)
  assign("run_prophet",     function(...) stop("prophet fail"), envir = .GlobalEnv)
  assign("run_holtwinters", function(...) stop("hw fail"),      envir = .GlobalEnv)
  on.exit({
    assign("run_arima",       orig_arima,   envir = .GlobalEnv)
    assign("run_prophet",     orig_prophet, envir = .GlobalEnv)
    assign("run_holtwinters", orig_hw,      envir = .GlobalEnv)
  }, add = TRUE)

  expect_error(run_ensemble(df, horizon = 7L), "All models failed")
})
