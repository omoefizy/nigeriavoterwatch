"""
Nigerian geographic hierarchy and election documents.

State → LGA → Ward → PollingUnit  (cross-referenced by PydanticObjectId)
Election  (scoped optionally to a State)
"""
import enum
from datetime import datetime
from typing import Annotated, Optional

from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class ElectionType(str, enum.Enum):
    PRESIDENTIAL = "presidential"
    GUBERNATORIAL = "gubernatorial"
    SENATORIAL = "senatorial"
    HOUSE_OF_REPS = "house_of_reps"
    STATE_ASSEMBLY = "state_assembly"
    LOCAL_GOVERNMENT = "local_government"


class ElectionStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    ONGOING = "ongoing"
    COLLATING = "collating"
    CONCLUDED = "concluded"
    SUSPENDED = "suspended"
    ANNULLED = "annulled"


class State(Document):
    name: Annotated[str, Indexed(unique=True)]
    code: Annotated[str, Indexed(unique=True)]
    geopolitical_zone: str
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None

    class Settings:
        name = "states"


class LGA(Document):
    name: str
    state_id: PydanticObjectId
    inec_lga_code: Optional[str] = None

    class Settings:
        name = "lgas"
        indexes = [
            IndexModel([("state_id", ASCENDING)]),
            IndexModel([("inec_lga_code", ASCENDING)]),
        ]


class Ward(Document):
    name: str
    lga_id: PydanticObjectId
    inec_ward_code: Optional[str] = None

    class Settings:
        name = "wards"
        indexes = [
            IndexModel([("lga_id", ASCENDING)]),
        ]


class PollingUnit(Document):
    name: str
    inec_pu_code: Annotated[str, Indexed(unique=True)]
    ward_id: PydanticObjectId
    registered_voters: int = 0
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool = True

    class Settings:
        name = "polling_units"
        indexes = [
            IndexModel([("ward_id", ASCENDING)]),
        ]


class Election(Document):
    name: str
    election_type: ElectionType
    status: ElectionStatus = ElectionStatus.SCHEDULED
    election_date: datetime
    scope_state_id: Optional[PydanticObjectId] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "elections"
        indexes = [
            IndexModel([("status", ASCENDING)]),
            IndexModel([("election_date", ASCENDING)]),
        ]
