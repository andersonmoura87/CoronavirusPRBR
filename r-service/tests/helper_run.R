# r-service/tests/helper_run.R — standalone test runner
#
# Named "helper_run.R" (not test*.R) so that testthat::test_dir() does NOT
# pick it up as a test file. Run manually with:
#   Rscript r-service/tests/helper_run.R        (from repo root)
#   Rscript /app/tests/helper_run.R             (inside the container)
#
# The CI uses an inline Rscript block in ci.yml instead of this file.

library(testthat)

args      <- commandArgs(trailingOnly = FALSE)
file_arg  <- grep("^--file=", args, value = TRUE)
test_path <- if (length(file_arg) > 0L) {
  dirname(normalizePath(sub("^--file=", "", file_arg[1L]), mustWork = FALSE))
} else {
  "."
}

results <- test_dir(path = test_path, reporter = "summary")

if (any(as.data.frame(results)$failed > 0L)) quit(status = 1L)
