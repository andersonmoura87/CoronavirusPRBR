"""
etl/scheduler.py — Nightly ETL scheduler via APScheduler.

Runs as a long-lived process (separate Docker service or sidecar) and
triggers the ETL pipeline on a cron schedule.

Default schedule:
  - COVID cases + vaccination: 03:00 BRT (06:00 UTC) daily
  - Economic indicators:       03:30 BRT (06:30 UTC) daily

brasil.io publishes updated case counts around midnight, so 3 AM gives
ample buffer. BCB and IBGE publish monthly — running daily is idempotent
(UPSERT) and cheap.

Usage:
  python -m etl.scheduler                  # run scheduler (blocks)
  python -m etl.scheduler --run-now        # fire all jobs once and exit

docker-compose adds this as an optional profile:
  docker compose --profile scheduler up
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from etl.ingest import run_all as run_ingest
from etl.economics import run_all as run_economics

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)
log = structlog.get_logger()

# ---------------------------------------------------------------------------
# Timezone — all crons in UTC; convert to BRT (UTC-3) mentally
# ---------------------------------------------------------------------------
TZ = "UTC"

# States to ingest for vaccination (comma-separated env var)
VACCINATION_STATES = os.environ.get("VACCINATION_STATES", "PR").upper().split(",")


# ---------------------------------------------------------------------------
# Job wrappers — APScheduler calls sync functions; we bridge to async
# ---------------------------------------------------------------------------

async def job_ingest() -> None:
    """
    Nightly COVID cases + vaccination ingestion.
    Runs for each state in VACCINATION_STATES sequentially.
    A failure in one state is logged but does not abort the others.
    """
    log.info("scheduler.ingest.start")
    for state in VACCINATION_STATES:
        try:
            await run_ingest(state_filter=state)
        except Exception as exc:
            log.error("scheduler.ingest.state_failed", state=state, error=str(exc))
    log.info("scheduler.ingest.done")


async def job_economics() -> None:
    """Nightly macroeconomic indicators refresh (IBGE + BCB)."""
    log.info("scheduler.economics.start")
    try:
        await run_economics()
    except Exception as exc:
        log.error("scheduler.economics.failed", error=str(exc))
    log.info("scheduler.economics.done")


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=TZ)

    # COVID cases + vaccination — 03:00 UTC daily (midnight BRT)
    scheduler.add_job(
        job_ingest,
        trigger=CronTrigger(hour=3, minute=0, timezone=TZ),
        id="ingest_daily",
        name="COVID cases + vaccination ingest",
        replace_existing=True,
        misfire_grace_time=3600,  # allow up to 1 h late start (e.g., container restart)
    )

    # Economic indicators — 03:30 UTC daily
    scheduler.add_job(
        job_economics,
        trigger=CronTrigger(hour=3, minute=30, timezone=TZ),
        id="economics_daily",
        name="Economic indicators ingest (IBGE + BCB)",
        replace_existing=True,
        misfire_grace_time=3600,
    )

    return scheduler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(run_now: bool = False) -> None:
    if run_now:
        log.info("scheduler.run_now")
        await job_ingest()
        await job_economics()
        log.info("scheduler.run_now.done")
        return

    scheduler = build_scheduler()
    scheduler.start()

    log.info(
        "scheduler.started",
        jobs=[j.name for j in scheduler.get_jobs()],
        next_runs={j.id: str(j.next_run_time) for j in scheduler.get_jobs()},
    )

    try:
        # Keep the event loop running until SIGTERM/SIGINT
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler.stopping")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    run_now = "--run-now" in sys.argv
    asyncio.run(main(run_now=run_now))
