# =============================================================================
# entrypoint.R — Docker container entry point
#
# Starts the plumber API on the port defined by the PORT environment variable
# (default 8001). Using an entrypoint file instead of `Rscript plumber.R`
# allows us to configure logging, signal handling, and health-check logic
# before the server starts.
#
# Signal handling note:
#   tini (PID 1) forwards SIGTERM to this Rscript process. Plumber's internal
#   httpuv server respects SIGTERM and shuts down gracefully, so Kubernetes
#   rolling updates drain connections cleanly within the terminationGracePeriod.
#
# Kubernetes securityContext recommendation (add to k8s/deployment.yaml):
#   securityContext:
#     runAsNonRoot: true
#     runAsUser: 1000          # UID of the 'rservice' user created in Dockerfile
#     readOnlyRootFilesystem: true
#     allowPrivilegeEscalation: false
#   volumeMounts:
#     - name: tmp
#       mountPath: /tmp        # R writes temp files here; needs to be writable
#     - name: home
#       mountPath: /home/rservice
#   volumes:
#     - name: tmp
#       emptyDir: {}
#     - name: home
#       emptyDir: {}
# =============================================================================

library(plumber)

port <- as.integer(Sys.getenv("PORT", unset = "8001"))
host <- Sys.getenv("HOST", unset = "0.0.0.0")

cat(sprintf(
  "[r-service] Starting plumber on %s:%d — R %s\n",
  host, port, R.version.string
))

# Resolve path relative to this file so the service works regardless of cwd
plumber_file <- file.path(dirname(normalizePath(sys.frame(0)$ofile, mustWork = FALSE)), "plumber.R")

pr <- plumb(plumber_file)

# Add a global error handler so any uncaught exception returns a structured
# JSON 500 instead of an empty response that confuses FastAPI.
pr$setErrorHandler(function(req, res, err) {
  cat(sprintf("[r-service] ERROR: %s\n", conditionMessage(err)))
  res$status <- 500L
  list(
    error   = "Internal R model error",
    message = conditionMessage(err),
    path    = req$PATH_INFO
  )
})

pr$run(host = host, port = port, swagger = TRUE)
