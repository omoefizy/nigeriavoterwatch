"""
APScheduler configuration.
Attach to FastAPI lifespan to start/stop with the app.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import structlog

from app.config import settings

logger = structlog.get_logger()

scheduler = AsyncIOScheduler(timezone="Africa/Lagos")


def setup_scheduler():
    from app.tasks.scraping_tasks import scrape_inec_results
    from app.tasks.integrity_tasks import verify_hash_chain
    from app.tasks.irev_tasks import scrape_irev_results

    scheduler.add_job(
        scrape_inec_results,
        trigger=IntervalTrigger(seconds=settings.scraper_interval_seconds),
        id="inec_scraper",
        name="INEC Results Scraper",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    scheduler.add_job(
        scrape_irev_results,
        trigger=IntervalTrigger(seconds=settings.irev_scraper_interval_seconds),
        id="irev_scraper",
        name="IReV EC8A Image Scraper",
        replace_existing=True,
        max_instances=1,       # never overlap — hierarchy traversal is stateful
        misfire_grace_time=120,
    )

    scheduler.add_job(
        verify_hash_chain,
        trigger=IntervalTrigger(minutes=settings.chain_verification_interval_minutes),
        id="chain_verifier",
        name="Hash Chain Integrity Verifier",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.start()
    logger.info("Scheduler started", jobs=len(scheduler.get_jobs()))


def teardown_scheduler():
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")
