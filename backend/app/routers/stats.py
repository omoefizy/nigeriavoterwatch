"""
Aggregated statistics endpoints for the Analytics dashboard.

GET /api/stats/upload-by-state   Upload coverage per state (from scrape log)
GET /api/stats/scrape-history    Recent IReV scrape run summaries
GET /api/stats/anomaly-summary   Anomaly counts grouped by state + severity
"""
from fastapi import APIRouter, Query

from app.models.observers import Anomaly, Observer, ObserverStatus
from app.models.results import ElectionResult
from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry

router = APIRouter()


@router.get("/upload-by-state")
async def upload_by_state():
    """
    Aggregate ScrapeLogEntry by state_name:
    returns total visited, images downloaded, missing URLs, and upload %.
    """
    pipeline = [
        {"$group": {
            "_id": "$state_name",
            "total": {"$sum": 1},
            "downloaded": {"$sum": {"$cond": ["$image_downloaded", 1, 0]}},
            "missing": {"$sum": {"$cond": ["$is_missing_url", 1, 0]}},
            "broken": {"$sum": {"$cond": ["$is_broken_url", 1, 0]}},
        }},
        {"$sort": {"_id": 1}},
    ]
    rows = await ScrapeLogEntry.get_pymongo_collection().aggregate(pipeline).to_list(None)
    return [
        {
            "state_name": r["_id"] or "Unknown",
            "total": r["total"],
            "downloaded": r["downloaded"],
            "missing": r["missing"],
            "broken": r["broken"],
            "upload_pct": round(r["downloaded"] / r["total"] * 100, 1) if r["total"] > 0 else 0,
        }
        for r in rows
    ]


@router.get("/scrape-history")
async def scrape_history(limit: int = Query(96, ge=1, le=500)):
    """Most-recent IReV scrape runs — each run is one 5-minute cycle."""
    runs = await IRevScrapeRun.find().sort("-started_at").limit(limit).to_list()
    return [
        {
            "id": str(r.id),
            "started_at": r.started_at.isoformat(),
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "status": r.status,
            "images_downloaded": r.images_downloaded,
            "total_visited": r.total_polling_units_visited,
            "missing_url_count": r.missing_url_count,
            "broken_url_count": r.broken_url_count,
        }
        for r in runs
    ]


@router.get("/anomaly-summary")
async def anomaly_summary():
    """Anomaly counts grouped by state_name and severity."""
    pipeline = [
        {"$group": {
            "_id": {"state": "$state_name", "severity": "$severity"},
            "count": {"$sum": 1},
            "unresolved": {"$sum": {"$cond": [{"$eq": ["$is_resolved", False]}, 1, 0]}},
        }},
        {"$sort": {"_id.state": 1}},
    ]
    rows = await Anomaly.get_pymongo_collection().aggregate(pipeline).to_list(None)

    # Pivot into {state_name, critical, warning, info, unresolved_total}
    states: dict = {}
    for r in rows:
        state = r["_id"]["state"] or "Unknown"
        severity = r["_id"]["severity"]
        if state not in states:
            states[state] = {"state_name": state, "critical": 0, "warning": 0, "info": 0, "total": 0, "unresolved": 0}
        states[state][severity] = r["count"]
        states[state]["total"] += r["count"]
        states[state]["unresolved"] += r["unresolved"]

    return sorted(states.values(), key=lambda x: x["state_name"])


@router.get("/dashboard")
async def dashboard():
    """Aggregated stats for the Home dashboard — total results, observers, anomalies, last scrape."""
    total_results, active_observers, open_anomalies, latest_runs = await _gather_dashboard()
    latest = latest_runs[0] if latest_runs else None
    return {
        "total_results": total_results,
        "active_observers": active_observers,
        "open_anomalies": open_anomalies,
        "latest_scrape": {
            "started_at": latest.started_at.isoformat() if latest else None,
            "completed_at": latest.completed_at.isoformat() if latest and latest.completed_at else None,
            "status": latest.status if latest else None,
            "images_downloaded": latest.images_downloaded if latest else 0,
        } if latest else None,
    }


async def _gather_dashboard():
    import asyncio
    return await asyncio.gather(
        ElectionResult.count(),
        Observer.find(Observer.status == ObserverStatus.VERIFIED).count(),
        Anomaly.find({"is_resolved": False}).count(),
        IRevScrapeRun.find().sort("-started_at").limit(1).to_list(),
    )
