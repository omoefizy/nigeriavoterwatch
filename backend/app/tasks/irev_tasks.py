"""
APScheduler task — runs the IReV scraper every 5 minutes,
then immediately runs anomaly detection on the completed run.
Beanie manages the MongoDB connection — no session lifecycle needed here.
"""
import structlog

logger = structlog.get_logger()


async def scrape_irev_results():
    """Entry point called by APScheduler (max_instances=1)."""
    logger.info("IReV scrape cycle starting")
    try:
        from app.scrapers.irev_scraper import IRevScraper
        scraper = IRevScraper()
        await scraper.run()
        logger.info("IReV scrape cycle committed")

        if scraper._run is not None:
            from app.services.anomaly_detector import AnomalyDetector
            detector = AnomalyDetector(scraper._run)
            count, critical_new = await detector.run()
            logger.info("Anomaly detection complete", new_anomalies=count, critical_new=critical_new)

            try:
                from app.ws.manager import manager
                run = scraper._run

                # results_update — always broadcast after a completed scrape
                await manager.broadcast({
                    "type": "results_update",
                    "run_id": str(run.id),
                    "images_downloaded": run.images_downloaded,
                    "missing_url_count": run.missing_url_count,
                    "broken_url_count": run.broken_url_count,
                    "new_anomalies": count,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                })

                # anomaly_alert — only when new CRITICAL anomalies were inserted
                if critical_new > 0:
                    from app.models.observers import Anomaly, AnomalySeverity
                    unresolved_critical = await Anomaly.find(
                        {"severity": AnomalySeverity.CRITICAL.value, "is_resolved": False}
                    ).count()
                    await manager.broadcast({
                        "type": "anomaly_alert",
                        "severity": "critical",
                        "new_count": critical_new,
                        "unresolved_critical_total": unresolved_critical,
                    })
            except Exception as ws_exc:
                logger.warning("WebSocket broadcast failed", error=str(ws_exc))

    except Exception as exc:
        logger.error("IReV scrape cycle failed", error=str(exc))
        raise
