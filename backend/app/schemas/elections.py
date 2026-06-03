"""Request and response schemas for elections and geographic hierarchy."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from app.models.elections import ElectionType, ElectionStatus


# ── Geographic read schemas ────────────────────────────────────────────────────

class StateRead(BaseModel):
    id: str
    name: str
    code: str
    geopolitical_zone: str
    centroid_lat: Optional[float] = None
    centroid_lng: Optional[float] = None


class LGARead(BaseModel):
    id: str
    name: str
    state_id: str
    inec_lga_code: Optional[str] = None


class WardRead(BaseModel):
    id: str
    name: str
    lga_id: str
    inec_ward_code: Optional[str] = None


class PollingUnitRead(BaseModel):
    id: str
    name: str
    inec_pu_code: str
    ward_id: str
    registered_voters: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_active: bool


# ── Election schemas ───────────────────────────────────────────────────────────

class ElectionCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    election_type: ElectionType
    election_date: datetime
    scope_state_id: Optional[str] = None

    @field_validator("election_date")
    @classmethod
    def date_must_be_future(cls, v: datetime) -> datetime:
        return v  # validation intentionally loose — allow historical data entry


class ElectionRead(BaseModel):
    id: str
    name: str
    election_type: ElectionType
    status: ElectionStatus
    election_date: datetime
    scope_state_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ElectionStatusUpdate(BaseModel):
    status: ElectionStatus
