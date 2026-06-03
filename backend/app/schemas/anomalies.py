"""Request and response schemas for anomalies."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.models.observers import AnomalyType, AnomalySeverity


class AnomalyRead(BaseModel):
    id: str
    election_id: str
    polling_unit_id: Optional[str] = None

    anomaly_type: AnomalyType
    severity: AnomalySeverity
    description: str
    details: Optional[dict] = None

    # Geographic context — denormalized for fast display
    state_name: Optional[str] = None
    lga_name: Optional[str] = None
    ward_name: Optional[str] = None

    source_result_id: Optional[str] = None
    source_report_id: Optional[str] = None

    is_resolved: bool
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None

    detected_at: datetime
    created_at: datetime


class AnomalyResolve(BaseModel):
    resolution_notes: str = Field(min_length=10, max_length=2000)
    resolved_by: str = Field(min_length=2, max_length=100)
