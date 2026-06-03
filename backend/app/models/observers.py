"""
Observer registration, field reports, and detected anomalies.
"""
import enum
from datetime import datetime
from typing import Annotated, Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class ObserverStatus(str, enum.Enum):
    PENDING = "pending"
    VERIFIED = "verified"
    SUSPENDED = "suspended"


class ObserverOrganization(str, enum.Enum):
    YIAGA = "yiaga"
    TRANSITION_MONITORING_GROUP = "tmg"
    CDD = "cdd"
    EU_OBSERVATION = "eu"
    AU_OBSERVATION = "au"
    ECOWAS = "ecowas"
    INDIVIDUAL = "individual"
    OTHER = "other"


class AnomalySeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AnomalyType(str, enum.Enum):
    # Vote-count integrity
    VOTE_INFLATION = "vote_inflation"         # C1: votes cast > accredited voters
    OVERCREDITATION = "overcreditation"       # C2: accredited > registered voters
    UNANIMOUS_RESULT = "unanimous_result"     # C3: single candidate gets 100%
    # Timeliness
    LATE_RESULT = "late_result"               # C4: image absent 2h after polls close
    # Statistical
    TURNOUT_ANOMALY = "turnout_anomaly"       # C5: z-score outlier vs LGA mean
    # Image integrity
    IMAGE_HASH_CHANGED = "image_hash_changed" # C6: EC8A image replaced after upload
    # Cross-source
    RESULT_DISCREPANCY = "result_discrepancy" # C7: OCR vs portal deviation > 20%
    # Catch-alls retained for observer / chain events
    MISSING_RESULT = "missing_result"
    OBSERVER_INCIDENT = "observer_incident"
    CHAIN_BREACH = "chain_breach"
    DUPLICATE_RESULT = "duplicate_result"


# ---------------------------------------------------------------------------
# Embedded sub-documents
# ---------------------------------------------------------------------------

class GpsLocation(BaseModel):
    latitude: float
    longitude: float
    accuracy_meters: Optional[float] = None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

class Observer(Document):
    full_name: str
    email: Annotated[str, Indexed(unique=True)]
    phone: Optional[str] = None
    password_hash: Optional[str] = None

    organization: ObserverOrganization = ObserverOrganization.INDIVIDUAL
    accreditation_id: Optional[str] = None
    status: ObserverStatus = ObserverStatus.PENDING

    assigned_polling_unit_id: Optional[PydanticObjectId] = None
    invite_code_hash: Optional[str] = None
    invite_code_expires_at: Optional[datetime] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_active_at: Optional[datetime] = None

    class Settings:
        name = "observers"
        indexes = [
            IndexModel([("status", ASCENDING)]),
            IndexModel([("assigned_polling_unit_id", ASCENDING)]),
        ]


class ObserverReport(Document):
    observer_id: PydanticObjectId
    election_id: PydanticObjectId
    polling_unit_id: PydanticObjectId

    accreditation_started_at: Optional[datetime] = None
    voting_started_at: Optional[datetime] = None
    voting_ended_at: Optional[datetime] = None
    estimated_turnout: Optional[int] = None

    violence_reported: bool = False
    ballot_stuffing_reported: bool = False
    underage_voting_reported: bool = False
    security_personnel_present: bool = True
    inec_officials_present: bool = True

    narrative: Optional[str] = None
    photo_paths: list[str] = Field(default_factory=list)

    location: Optional[GpsLocation] = None

    is_reviewed: bool = False
    reviewed_by: Optional[str] = None

    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "observer_reports"
        indexes = [
            IndexModel([("observer_id", ASCENDING)]),
            IndexModel([("election_id", ASCENDING)]),
            IndexModel([("polling_unit_id", ASCENDING)]),
            IndexModel([("created_at", ASCENDING)]),
        ]


class Anomaly(Document):
    election_id: PydanticObjectId
    polling_unit_id: Optional[PydanticObjectId] = None

    anomaly_type: AnomalyType
    severity: AnomalySeverity

    description: str
    # evidence — the actual numbers / hashes that triggered the flag
    details: Optional[dict] = None

    # Denormalized geography for fast display without joins
    state_name: Optional[str] = None
    lga_name: Optional[str] = None
    ward_name: Optional[str] = None

    source_result_id: Optional[PydanticObjectId] = None
    source_report_id: Optional[PydanticObjectId] = None

    is_resolved: bool = False
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None

    detected_at: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "anomalies"
        indexes = [
            IndexModel([("election_id", ASCENDING)]),
            IndexModel([("anomaly_type", ASCENDING)]),
            IndexModel([("severity", ASCENDING)]),
            IndexModel([("is_resolved", ASCENDING)]),
            IndexModel([("state_name", ASCENDING)]),
            IndexModel([("detected_at", ASCENDING)]),
        ]
