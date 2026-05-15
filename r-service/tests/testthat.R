# r-service/tests/testthat.R — test runner entry point
# Run with: Rscript r-service/tests/testthat.R
# Or from inside the container: Rscript /app/tests/testthat.R

library(testthat)

test_dir(
  path    = dirname(sys.frame(0)$ofile),
  reporter = "summary"
)
