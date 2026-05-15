# =============================================================================
# install_packages.R — R package installation + renv lockfile snapshot
#
# Executed once during Docker image build (builder stage only).
#
# Reproducibility layers (defense-in-depth):
#   Layer 1 — CRAN snapshot date via Posit PPM (coarse: pins all packages to
#              versions available on that date).
#   Layer 2 — renv::snapshot() after install (fine: records exact version of
#              every package + transitive dependency in renv.lock).
#
# To restore an environment exactly: renv::restore(lockfile = "renv.lock").
# CI can compare the committed renv.lock against a fresh snapshot to detect
# unexpected drift.
# =============================================================================

snapshot_date <- Sys.getenv("CRAN_SNAPSHOT_DATE", unset = "2024-06-01")

# Posit Public Package Manager frozen snapshot — avoids MRAN deprecation
cran_url <- sprintf(
  "https://packagemanager.posit.co/cran/%s",
  snapshot_date
)

options(
  repos              = c(CRAN = cran_url),
  Ncpus              = parallel::detectCores(),
  # Prefer pre-compiled binaries; fall back to source only if unavailable.
  install.packages.compile.from.source = "never"
)

cat(sprintf("[install_packages] CRAN snapshot : %s\n", cran_url))
cat(sprintf("[install_packages] R version     : %s\n", R.version.string))

# ---------------------------------------------------------------------------
# Core application packages
# ---------------------------------------------------------------------------

pkgs <- c(
  # API framework
  "plumber",
  "jsonlite",

  # Time-series forecasting
  "forecast",
  "prophet",
  "zoo",

  # Data wrangling
  "dplyr",
  "tidyr",
  "lubridate",
  "readr",

  # Econometrics / statistics
  "broom",
  "lmtest",
  "sandwich",

  # Utilities
  "logger",

  # renv itself — needed to write the lockfile inside the builder stage
  "renv"
)

cat(sprintf("[install_packages] Installing %d packages...\n", length(pkgs)))

install.packages(pkgs, dependencies = TRUE, quiet = FALSE, ask = FALSE)

# ---------------------------------------------------------------------------
# Verify all primary packages installed
# ---------------------------------------------------------------------------

installed_pkgs <- rownames(installed.packages())
failed <- pkgs[!pkgs %in% installed_pkgs]

if (length(failed) > 0L) {
  stop(sprintf("[install_packages] FAILED: %s", paste(failed, collapse = ", ")))
}

cat("[install_packages] All packages installed successfully.\n")

# Print versions for the Docker build log (grep-able in CI)
cat("\n[install_packages] Installed versions:\n")
for (p in pkgs) {
  cat(sprintf("  %-20s %s\n", p, packageVersion(p)))
}

# ---------------------------------------------------------------------------
# renv snapshot — Layer 2 reproducibility
#
# Writes /install/renv.lock capturing the exact version of every installed
# package (including transitive dependencies not listed above).
# The Dockerfile COPYs this file into the runtime image for audit purposes,
# and CI can use it to verify the build is reproducible:
#   docker run --rm r-service Rscript -e "renv::restore()"
# ---------------------------------------------------------------------------

cat("\n[install_packages] Writing renv.lock...\n")

# Point renv at the system library (not a project-local library)
renv::snapshot(
  library  = .libPaths()[1],
  lockfile = "/install/renv.lock",
  prompt   = FALSE,
  force    = TRUE
)

cat("[install_packages] renv.lock written to /install/renv.lock\n")
