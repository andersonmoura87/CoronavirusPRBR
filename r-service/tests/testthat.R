# r-service/tests/testthat.R — test runner entry point
# Run with: Rscript r-service/tests/testthat.R  (from repo root)
# Or from inside the container: Rscript /app/tests/testthat.R

library(testthat)

# When called via `Rscript file.R`, sys.frame(0)$ofile is NULL.
# Derive the test directory from the --file= argument instead.
args      <- commandArgs(trailingOnly = FALSE)
file_arg  <- grep("^--file=", args, value = TRUE)
test_path <- if (length(file_arg) > 0L) {
  dirname(normalizePath(sub("^--file=", "", file_arg[1L]), mustWork = FALSE))
} else {
  "."
}

results <- test_dir(path = test_path, reporter = "summary")

if (any(as.data.frame(results)$failed > 0L)) quit(status = 1L)
