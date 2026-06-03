"""
Write-once audit log and hash chain mirror.

AuditLog rows are NEVER modified after insert — enforced at the application layer.
HashChainEntry mirrors ElectionResult integrity fields for fast O(n) chain verification.
"""
import enum
from datetime import datetime
from typing import Annotated, Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class AuditAction(str, enum.Enum):
    RESULT_CREATED = "result_created"
    RESULT_SUPERSEDED = "result_superseded"
    RESULT_STATUS_CHANGED = "result_status_changed"
    DOCUMENT_UPLOADED = "document_uploaded"
    OCR_COMPLETED = "ocr_completed"
    OBSERVER_REGISTERED = "observer_registered"
    OBSERVER_REPORT_SUBMITTED = "observer_report_submitted"
    ANOMALY_FLAGGED = "anomaly_flagged"
    ANOMALY_RESOLVED = "anomaly_resolved"
    ELECTION_CREATED = "election_created"
    ELECTION_STATUS_CHANGED = "election_status_changed"
    SCRAPE_STARTED = "scrape_started"
    SCRAPE_COMPLETED = "scrape_completed"
    SCRAPE_FAILED = "scrape_failed"
    CHAIN_VERIFIED = "chain_verified"
    CHAIN_BREACH_DETECTED = "chain_breach_detected"
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    ADMIN_ACTION = "admin_action"


class AuditLog(Document):
    action: AuditAction

    actor_type: str
    actor_id: Optional[str] = None

    entity_type: Optional[str] = None
    entity_id: Optional[str] = None

    details: Optional[dict] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "audit_log"
        indexes = [
            IndexModel([("created_at", ASCENDING)]),
            IndexModel([("action", ASCENDING)]),
            IndexModel([("actor_type", ASCENDING), ("actor_id", ASCENDING)]),
            IndexModel([("entity_type", ASCENDING), ("entity_id", ASCENDING)]),
        ]


class HashChainEntry(Document):
    """
    Denormalized mirror of ElectionResult integrity fields.
    Allows O(n) chain verification without joining election_results.
    """
    result_id: PydanticObjectId  # references ElectionResult._id
    sequence: Annotated[int, Indexed(unique=True)]

    election_id: PydanticObjectId
    polling_unit_id: PydanticObjectId

    content_hash: str
    prev_hash: str
    block_hash: Annotated[str, Indexed(unique=True)]

    is_verified: bool = True
    verified_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "hash_chain_entries"
        indexes = [
            IndexModel([("sequence", ASCENDING)], unique=True),
            IndexModel([("block_hash", ASCENDING)], unique=True),
        ]
