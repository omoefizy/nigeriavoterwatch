"""
Scheduled INEC portal scraping task.
Runs on the APScheduler interval defined in settings.scraper_interval_seconds.
Beanie manages the MongoDB connection — no session lifecycle needed here.
"""
import structlog

logger = structlog.get_logger()


async def scrape_inec_results():
    """Entry point called by APScheduler."""
    logger.info("Starting INEC scrape cycle")
    try:
        from app.scrapers.inec_scraper import INECScraper
        scraper = INECScraper()
        await scraper.run()
        logger.info("INEC scrape cycle completed")
    except Exception as exc:
        logger.error("INEC scrape cycle failed", error=str(exc))
        raise
