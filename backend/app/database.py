"""
MongoDB connection via Motor + Beanie ODM.

Call `init_db()` once during FastAPI lifespan startup.
All Beanie Documents are immediately usable after that — no session objects needed.
"""
from typing import Optional
import motor.motor_asyncio
from beanie import init_beanie
import structlog

from app.config import settings

logger = structlog.get_logger()

_motor_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None


async def init_db() -> motor.motor_asyncio.AsyncIOMotorClient:
    """Connect Motor, initialise Beanie with all document models."""
    global _motor_client

    # Import here to avoid circular imports at module level
    from app.models.elections import State, LGA, Ward, PollingUnit, Election
    from app.models.results import ElectionResult, ResultDocument
    from app.models.audit import AuditLog, HashChainEntry
    from app.models.observers import Observer, ObserverReport, Anomaly
    from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry
    from app.models.user import User

    _motor_client = motor.motor_asyncio.AsyncIOMotorClient(settings.mongodb_uri)
    db = _motor_client[settings.mongodb_db_name]

    await init_beanie(
        database=db,
        document_models=[
            State, LGA, Ward, PollingUnit, Election,
            ElectionResult, ResultDocument,
            AuditLog, HashChainEntry,
            Observer, ObserverReport, Anomaly,
            IRevScrapeRun, ScrapeLogEntry,
            User,
        ],
    )

    logger.info("MongoDB connected", db=settings.mongodb_db_name)
    return _motor_client


async def close_db():
    global _motor_client
    if _motor_client is not None:
        _motor_client.close()
        _motor_client = None
        logger.info("MongoDB connection closed")
