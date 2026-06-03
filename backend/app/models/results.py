"""
Append-only election results with hash chain for tamper detection.

ElectionResult rows are NEVER updated or deleted.
To correct a result: insert a new row with supersedes_id pointing to the old one
and update the old row's status to SUPERSEDED.

Current result query: {supersedes_id: null, status: {$ne: "superseded"}}
"""
import enum
from datetime import datetime
from typing import Annotated, Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel


class ResultSource(str, enum.Enum):
    INEC_PORTAL = "inec_portal"
    INEC_SCRAPER = "inec_scraper"
    OBSERVER_REPORT = "observer_report"
    OCR_DOCUMENT = "ocr_document"
    MANUAL_ENTRY = "manual_entry"


class ResultStatus(str, enum.Enum):
    PRELIMINARY = "preliminary"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    SUPERSEDED = "superseded"


class ElectionResult(Document):
    election_id: PydanticObjectId
    polling_unit_id: PydanticObjectId

    party_votes: dict[str, int] = Field(default_factory=dict)
    accredited_voters: int = 0
    total_votes_cast: int = 0
    valid_votes: int = 0
    rejected_votes: int = 0

    source: ResultSource
    status: ResultStatus = ResultStatus.PRELIMINARY
    source_url: Optional[str] = None
    document_id: Optional[PydanticObjectId] = None
    supersedes_id: Optional[PydanticObjectId] = None

    # Integrity fields — set at insert, never changed
    content_hash: str
    prev_hash: str
    chain_sequence: Annotated[int, Indexed(unique=True)]

    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None

    class Settings:
        name = "election_results"
        indexes = [
            IndexModel([("election_id", ASCENDING), ("polling_unit_id", ASCENDING)]),
            IndexModel([("content_hash", ASCENDING)]),
            IndexModel([("chain_sequence", ASCENDING)], unique=True),
            IndexModel([("status", ASCENDING)]),
        ]


class ResultDocument(Document):
    election_id: PydanticObjectId
    polling_unit_id: Optional[PydanticObjectId] = None

    filename: str
    storage_path: str
    file_hash_sha256: str
    mime_type: str
    file_size_bytes: int

    ocr_raw_text: Optional[str] = None
    ocr_confidence: Optional[float] = None
    ocr_engine: Optional[str] = None

    source_url: Optional[str] = None
    uploaded_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "result_documents"
        indexes = [
            IndexModel([("election_id", ASCENDING)]),
            IndexModel([("file_hash_sha256", ASCENDING)]),
        ]
