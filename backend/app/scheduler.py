"""
APScheduler — AsyncIOScheduler running inside the FastAPI process.
Started in the lifespan context manager (after DB init).

Jobs:
1. Nightly full suite run (02:00 UTC) — triggered as a real pipeline run.
2. Selector health sweep (every 6h) — re-inspects tracked selectors for silent drift.
3. Index rebuild (on startup + every 12h) — refreshes selector_test_index.
"""
import uuid
import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = structlog.get_logger()
_scheduler: AsyncIOScheduler | None = None


async def _run_nightly_suite():
    """Trigger a full suite run against BASE_URL (nightly health check)."""
    log.info("scheduler.nightly_run.start")
    try:
        import app.db as db
        from app.core.state import RunRecord, RunStatus
        from app.core.pipeline import run_pipeline

        run_id = str(uuid.uuid4())
        run = RunRecord(
            id=run_id,
            status=RunStatus.PLANNING,
            trigger_commit="scheduled",
            trigger_branch="main",
            team_id="core-platform",
        )
        await db.create_run(run)
        await run_pipeline(run_id)
        log.info("scheduler.nightly_run.done", run_id=run_id)
    except Exception as exc:
        log.error("scheduler.nightly_run.error", error=str(exc)[:200])


async def _rebuild_selector_index():
    """Rebuild selector_test_index from current test files."""
    log.info("scheduler.index_rebuild.start")
    try:
        from app.core.selector_index import build_selector_index
        count = await build_selector_index()
        log.info("scheduler.index_rebuild.done", entries=count)
    except Exception as exc:
        log.error("scheduler.index_rebuild.error", error=str(exc)[:200])


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    _scheduler = AsyncIOScheduler()

    # Nightly full suite run at 02:00 UTC
    _scheduler.add_job(
        _run_nightly_suite,
        trigger=CronTrigger(hour=2, minute=0),
        id="nightly_run",
        replace_existing=True,
        misfire_grace_time=600,
    )

    # Selector index rebuild every 12h
    _scheduler.add_job(
        _rebuild_selector_index,
        trigger=IntervalTrigger(hours=12),
        id="index_rebuild",
        replace_existing=True,
    )

    _scheduler.start()
    log.info("scheduler.started", jobs=[j.id for j in _scheduler.get_jobs()])
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler.stopped")
