"""
Set required environment variables before any app module is imported.
These defaults are safe for unit tests that do not touch MongoDB.
"""
import os

os.environ.setdefault("GENESIS_BLOCK_SEED", "ci-test-genesis-2027")
os.environ.setdefault("MONGODB_URI",        "mongodb://localhost:27017")
os.environ.setdefault("MONGODB_DB_NAME",    "test_nigeriavoterwatch")
os.environ.setdefault("JWT_SECRET_KEY",     "ci-test-jwt-secret-key")
os.environ.setdefault("APP_SECRET_KEY",     "ci-test-app-secret-key")

import uuid

import pytest_asyncio


@pytest_asyncio.fixture
async def mock_db():
    """
    Fresh in-memory MongoDB (mongomock-motor) with Beanie initialised on all
    document models. Runs the real Beanie queries and aggregation pipelines
    without a server — never touches Atlas.
    """
    from beanie import init_beanie
    from mongomock_motor import AsyncMongoMockClient

    from app.models.audit import AuditLog, HashChainEntry
    from app.models.elections import LGA, Election, PollingUnit, State, Ward
    from app.models.observers import Anomaly, Observer, ObserverReport
    from app.models.results import ElectionResult, ResultDocument
    from app.models.scrape_log import IRevScrapeRun, ScrapeLogEntry
    from app.models.user import User

    client = AsyncMongoMockClient()
    db = client[f"test_{uuid.uuid4().hex}"]
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
    yield db
