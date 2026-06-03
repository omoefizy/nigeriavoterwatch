"""Request and response schemas for election results."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from app.models.results import ResultSource, ResultStatus


class ResultRead(BaseModel):
    id: str
    election_id: str
    polling_unit_id: str

    # Denormalized for display — populated by the router's batch geo lookup
    inec_pu_code: Optional[str] = None
    pu_name: Optional[str] = None
    state_name: Optional[str] = None
    lga_name: Optional[str] = None
    ward_name: Optional[str] = None

    party_votes: dict
    accredited_voters: int
    total_votes_cast: int
    valid_votes: int
    rejected_votes: int

    source: ResultSource
    status: ResultStatus
    source_url: Optional[str] = None
    supersedes_id: Optional[str] = None

    # Integrity
    content_hash: str
    chain_sequence: int

    created_at: datetime
    created_by: Optional[str] = None


class ElectionSummaryRead(BaseModel):
    election_id: str
    total_polling_units_with_results: int
    total_accredited_voters: int
    total_votes_cast: int
    total_valid_votes: int
    total_rejected_votes: int
    party_totals: dict
    results_by_status: dict
