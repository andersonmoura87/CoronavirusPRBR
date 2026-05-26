# =============================================================================
# r-service/Dockerfile
#
# Multi-stage build for the R plumber microservice.
#
# Stage 1 (builder) — full toolchain, installs R packages + generates renv lockfile.
# Stage 2 (runtime) — copies compiled libraries + source only; no headers.
#
# Hardening applied (see inline comments for rationale on each decision):
#   [1] --no-install-recommends + --no-install-suggests → smaller attack surface
#   [2] libv8-dev only in builder; runtime copies .so files directly
#   [3] tini as PID 1 → correct signal forwarding + no zombie processes
#   [4] curl-based HEALTHCHECK → no R package dependency at probe time
#   [5] ENV HOME set explicitly → Kubernetes runAsNonRoot + readOnlyRootFilesystem safe
#   [6] renv lockfile snapshot → exact package versions, beyond CRAN date pinning
# =============================================================================

# ── Stage 1: builder (full toolchain) ────────────────────────────────────────
FROM rocker/r-ver:4.3.3 AS builder

ARG CRAN_SNAPSHOT_DATE=2024-06-01
ENV CRAN_SNAPSHOT_DATE=${CRAN_SNAPSHOT_DATE}

# [1] --no-install-recommends AND --no-install-suggests — both flags together
#     minimise transitive packages pulled into the build layer.
RUN apt-get update && apt-get install -y \
      --no-install-recommends \
      --no-install-suggests \
    libssl-dev \
    libcurl4-openssl-dev \
    libxml2-dev \
    libv8-dev \
    libgit2-dev \
    libfontconfig1-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libfreetype6-dev \
    libpng-dev \
    libtiff5-dev \
    libjpeg-dev \
    libgmp-dev \
    libsodium-dev \
    libudunits2-dev \
    libpq-dev \
    libcairo2-dev \
    libcairo-gobject2 \
    default-libmysqlclient-dev \
    cmake \
    g++ \
    make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /install

COPY install_packages.R .
# renv.lock is optional on first build — install_packages.R generates a fresh one.
# On subsequent builds the committed lockfile is available for comparison / audit.
COPY renv.lock* ./

# Install packages and snapshot exact versions into /install/renv.lock.
# [6] The generated lockfile is exported via `docker build --output` in the
#     Makefile target `make renv-update` (see README) so it can be committed.
RUN Rscript install_packages.R

# Export the generated renv.lock so it can be retrieved with:
#   docker build --target builder --output type=local,dest=. -f r-service/Dockerfile r-service/
# and committed to the repo as the authoritative lockfile.
# The file stays in /install/renv.lock inside this stage.

# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM rocker/r-ver:4.3.3

# [1] Both flags here too — runtime base image should stay minimal.
# [3] tini installed here; it becomes the container's PID 1 via ENTRYPOINT.
#     Without tini, Rscript runs as PID 1 and cannot forward SIGTERM correctly,
#     causing Kubernetes rolling updates to wait 30 s for the grace period.
# [4] curl installed for the HEALTHCHECK probe — no R package dependency.
RUN apt-get update && apt-get install -y \
      --no-install-recommends \
      --no-install-suggests \
    libssl3 \
    libcurl4 \
    libxml2 \
    curl \
    tini \
    && rm -rf /var/lib/apt/lists/*

# [2] Copy V8 shared libraries from builder instead of installing libv8-dev
#     (which pulls headers and build tools into the runtime image).
#     On Debian Bookworm, libv8-dev does not have a separate runtime-only package,
#     so we extract only the .so files via COPY --from=builder.
COPY --from=builder /usr/lib/x86_64-linux-gnu/libv8* \
                    /usr/lib/x86_64-linux-gnu/

# Copy the full R library from the builder stage
COPY --from=builder /usr/local/lib/R/library /usr/local/lib/R/library

# Create non-root user with explicit HOME
# [5] Setting HOME explicitly keeps R's tempdir + .Rprofile inside a writable
#     directory; required when Kubernetes mounts readOnlyRootFilesystem: true.
RUN useradd --create-home --shell /bin/bash rservice
ENV HOME=/home/rservice

WORKDIR /app
RUN chown rservice:rservice /app

# Copy application source files
COPY --chown=rservice:rservice plumber.R     ./plumber.R
COPY --chown=rservice:rservice entrypoint.R  ./entrypoint.R
COPY --chown=rservice:rservice models/       ./models/
COPY --chown=rservice:rservice renv.lock*    ./

USER rservice

EXPOSE 8001

ENV PORT=8001
ENV HOST=0.0.0.0

# [4] curl-based probe — does not depend on any R package being importable.
#     Returns exit 0 only when the /health endpoint responds 2xx.
#     --fail makes curl exit non-zero on HTTP 4xx/5xx (not just network errors).
#     --silent suppresses progress output; --max-time 5 avoids hanging the probe.
HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \
    CMD curl --fail --silent --max-time 5 http://localhost:8001/health || exit 1

# [3] tini as ENTRYPOINT; CMD is passed as its argument.
#     tini reaps orphaned child processes and forwards signals to the R process.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["Rscript", "entrypoint.R"]
