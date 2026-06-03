"""
IReV scrape run and per-polling-unit log documents.

IRevScrapeRun  — one document per 5-minute scrape cycle.
ScrapeLogEntry — one document per polling unit visited; carries the image hash chain.

Image chain rule (independent of the results hash chain):
    image_block_hash = SHA-256(image_hash_sha256 + prev_image_block_hash)
    Genesis prev = SHA-256(GENESIS_BLOCK_SEED + ":irev-images")
"""
import enum
from datetime import datetime
from typing import Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class ScrapeRunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


class IRevScrapeRun(Document):
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    status: ScrapeRunStatus = ScrapeRunStatus.RUNNING

    total_polling_units_visited: int = 0
    images_downloaded: int = 0
    missing_url_count: int = 0
    broken_url_count: int = 0

    chain_tip_hash: Optional[str] = None
    chain_tip_sequence: Optional[int] = None

    snapshot_written_at: Optional[datetime] = None
    error_message: Optional[str] = None

    class Settings:
        name = "irev_scrape_runs"
        indexes = [
            IndexModel([("started_at", ASCENDING)]),
            IndexModel([("status", ASCENDING)]),
        ]


class ScrapeLogEntry(Document):
    """
    Append-only record of one polling unit visit.
    Never updated or deleted after insert.
    """
    run_id: PydanticObjectId

    # Geographic hierarchy (mirrors INEC PU code: STATE/LGA/WARD/SEQ)
    state_code: str
    state_name: str
    lga_code: Optional[str] = None
    lga_name: Optional[str] = None
    ward_code: Optional[str] = None
    ward_name: Optional[str] = None
    pu_code: Optional[str] = None
    pu_name: Optional[str] = None

    # IReV portal context
    portal_url: Optional[str] = None

    # EC8A image data
    image_url: Optional[str] = None
    image_downloaded: bool = False
    image_hash_sha256: Optional[str] = None
    image_size_bytes: Optional[int] = None
    image_stored_path: Optional[str] = None

    # Image hash chain — None until an image is successfully downloaded
    chain_sequence: Optional[int] = None    # indexed via Settings.indexes
    prev_image_block_hash: Optional[str] = None
    image_block_hash: Optional[str] = None

    # Anomaly flags
    is_missing_url: bool = False
    is_broken_url: bool = False
    http_status: Optional[int] = None

    scraped_at: datetime = Field(default_factory=datetime.utcnow)
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None

    class Settings:
        name = "scrape_log"
        indexes = [
            IndexModel([("run_id", ASCENDING)]),
            IndexModel([("pu_code", ASCENDING)]),
            IndexModel([("scraped_at", ASCENDING)]),
            IndexModel([("chain_sequence", ASCENDING)]),
            IndexModel([("is_missing_url", ASCENDING), ("is_broken_url", ASCENDING)]),
        ]
