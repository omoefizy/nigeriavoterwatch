"""Request and response schemas for observers and field reports."""
import re
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from app.models.observers import ObserverOrganization, ObserverStatus, GpsLocation


_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


# ── Observer schemas ───────────────────────────────────────────────────────────

class ObserverInvite(BaseModel):
    """Admin-only: pre-create a pending observer slot and return a one-time code."""
    email: str = Field(max_length=254)
    full_name: str = Field(min_length=2, max_length=200)
    assigned_polling_unit_id: Optional[str] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()


class ObserverRegister(BaseModel):
    """Observer completes their profile using a one-time invite code."""
    email: str = Field(max_length=254)
    invite_code: str = Field(min_length=8, max_length=128)
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=2, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=20)
    organization: ObserverOrganization = ObserverOrganization.INDIVIDUAL
    accreditation_id: Optional[str] = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("Invalid email address")
        return v.lower()

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        digits = re.sub(r"\D", "", v)
        if not (7 <= len(digits) <= 15):
            raise ValueError("Phone must contain 7–15 digits")
        return v


class ObserverRead(BaseModel):
    id: str
    full_name: str
    email: str
    phone: Optional[str] = None
    organization: ObserverOrganization
    accreditation_id: Optional[str] = None
    status: ObserverStatus
    assigned_polling_unit_id: Optional[str] = None
    created_at: datetime


# ── Observer report schemas ────────────────────────────────────────────────────

class GpsLocationIn(BaseModel):
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    accuracy_meters: Optional[float] = Field(default=None, ge=0.0)

    def to_embedded(self) -> GpsLocation:
        return GpsLocation(
            latitude=self.latitude,
            longitude=self.longitude,
            accuracy_meters=self.accuracy_meters,
        )


class ReportSubmit(BaseModel):
    election_id: str
    polling_unit_id: str

    accreditation_started_at: Optional[datetime] = None
    voting_started_at: Optional[datetime] = None
    voting_ended_at: Optional[datetime] = None
    estimated_turnout: Optional[int] = Field(default=None, ge=0, le=500_000)

    violence_reported: bool = False
    ballot_stuffing_reported: bool = False
    underage_voting_reported: bool = False
    security_personnel_present: bool = True
    inec_officials_present: bool = True

    narrative: Optional[str] = Field(default=None, max_length=5000)
    photo_paths: list[str] = Field(default_factory=list, max_length=10)

    location: Optional[GpsLocationIn] = None

    @field_validator("voting_ended_at")
    @classmethod
    def end_after_start(cls, v: Optional[datetime], info) -> Optional[datetime]:
        start = (info.data or {}).get("voting_started_at")
        if v and start and v < start:
            raise ValueError("voting_ended_at must be after voting_started_at")
        return v

    @field_validator("photo_paths")
    @classmethod
    def validate_photo_paths(cls, v: list) -> list:
        for path in v:
            if not isinstance(path, str) or len(path) > 512:
                raise ValueError("Each photo_path must be a string ≤512 chars")
        return v


class ReportRead(BaseModel):
    id: str
    observer_id: str
    election_id: str
    polling_unit_id: str

    accreditation_started_at: Optional[datetime] = None
    voting_started_at: Optional[datetime] = None
    voting_ended_at: Optional[datetime] = None
    estimated_turnout: Optional[int] = None

    violence_reported: bool
    ballot_stuffing_reported: bool
    underage_voting_reported: bool
    security_personnel_present: bool
    inec_officials_present: bool

    narrative: Optional[str] = None
    photo_paths: list[str]

    location: Optional[GpsLocation] = None
    is_reviewed: bool
    created_at: datetime
