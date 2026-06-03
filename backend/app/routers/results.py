"""
Election results endpoints.

GET /api/results                             paginated, multi-level filter
GET /api/results/{result_id}                 single result with integrity proof
GET /api/results/election/{election_id}/summary  aggregated totals + party breakdown
"""
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Query

from app.models.elections import Election, LGA, PollingUnit, State, Ward
from app.models.results import ElectionResult, ResultStatus
from app.schemas.common import PaginatedResponse, make_pages, not_found, to_oid
from app.schemas.results import ElectionSummaryRead, ResultRead

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _result_read(
    r: ElectionResult,
    pu_map: dict = {},
    ward_map: dict = {},
    lga_map: dict = {},
    state_map: dict = {},
) -> ResultRead:
    pu = pu_map.get(r.polling_unit_id)
    ward = ward_map.get(pu.ward_id) if pu else None
    lga = lga_map.get(ward.lga_id) if ward else None
    state = state_map.get(lga.state_id) if lga else None
    return ResultRead(
        id=str(r.id),
        election_id=str(r.election_id),
        polling_unit_id=str(r.polling_unit_id),
        inec_pu_code=pu.inec_pu_code if pu else None,
        pu_name=pu.name if pu else None,
        state_name=state.name if state else None,
        lga_name=lga.name if lga else None,
        ward_name=ward.name if ward else None,
        party_votes=r.party_votes,
        accredited_voters=r.accredited_voters,
        total_votes_cast=r.total_votes_cast,
        valid_votes=r.valid_votes,
        rejected_votes=r.rejected_votes,
        source=r.source,
        status=r.status,
        source_url=r.source_url,
        supersedes_id=str(r.supersedes_id) if r.supersedes_id else None,
        content_hash=r.content_hash,
        chain_sequence=r.chain_sequence,
        created_at=r.created_at,
        created_by=r.created_by,
    )


async def _pu_ids_for_ward(ward_id_str: str) -> Optional[list]:
    """Return list of PollingUnit ObjectIds belonging to a ward, or None on bad id."""
    ward_oid = to_oid(ward_id_str)
    pus = await PollingUnit.find(
        PollingUnit.ward_id == ward_oid
    ).project(PollingUnit).to_list()
    return [p.id for p in pus]


async def _pu_ids_for_lga(lga_id_str: str) -> Optional[list]:
    """Return list of PollingUnit ObjectIds belonging to an LGA (two-hop lookup)."""
    lga_oid = to_oid(lga_id_str)
    wards = await Ward.find(Ward.lga_id == lga_oid).to_list()
    ward_oids = [w.id for w in wards]
    if not ward_oids:
        return []
    pus = await PollingUnit.find({"ward_id": {"$in": ward_oids}}).to_list()
    return [p.id for p in pus]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[ResultRead])
async def list_results(
    election_id: Optional[str] = None,
    polling_unit_id: Optional[str] = None,
    ward_id: Optional[str] = None,
    lga_id: Optional[str] = None,
    status: Optional[ResultStatus] = None,
    exclude_superseded: bool = True,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    # Build filter conditions progressively
    raw: dict = {}

    if election_id:
        raw["election_id"] = to_oid(election_id)

    # Geographic filter (most-specific wins; ward_id overrides lga_id)
    if polling_unit_id:
        raw["polling_unit_id"] = to_oid(polling_unit_id)
    elif ward_id:
        pu_ids = await _pu_ids_for_ward(ward_id)
        raw["polling_unit_id"] = {"$in": pu_ids}
    elif lga_id:
        pu_ids = await _pu_ids_for_lga(lga_id)
        raw["polling_unit_id"] = {"$in": pu_ids}

    if status:
        raw["status"] = status.value
    elif exclude_superseded:
        raw["status"] = {"$ne": ResultStatus.SUPERSEDED.value}

    # Only current (non-superseded) head rows by default
    if exclude_superseded:
        raw["supersedes_id"] = None

    q = ElectionResult.find(raw)
    total = await q.count()
    items = await q.sort("-chain_sequence").skip((page - 1) * page_size).limit(page_size).to_list()

    # Batch geo lookup — 4 queries regardless of page size
    pu_ids = list({r.polling_unit_id for r in items})
    pus = await PollingUnit.find({"_id": {"$in": pu_ids}}).to_list()
    pu_map = {p.id: p for p in pus}

    ward_ids = list({p.ward_id for p in pus})
    wards = await Ward.find({"_id": {"$in": ward_ids}}).to_list()
    ward_map = {w.id: w for w in wards}

    lga_ids = list({w.lga_id for w in wards})
    lgas = await LGA.find({"_id": {"$in": lga_ids}}).to_list()
    lga_map = {lg.id: lg for lg in lgas}

    state_ids = list({lg.state_id for lg in lgas})
    states = await State.find({"_id": {"$in": state_ids}}).to_list()
    state_map = {s.id: s for s in states}

    return PaginatedResponse(
        items=[_result_read(r, pu_map, ward_map, lga_map, state_map) for r in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=make_pages(total, page_size),
    )


@router.get("/election/{election_id}/summary", response_model=ElectionSummaryRead)
async def election_summary(election_id: str):
    election_oid = to_oid(election_id)
    if not await Election.get(election_oid):
        raise not_found("Election", election_id)

    # Aggregate numeric totals via MongoDB pipeline
    pipeline = [
        {
            "$match": {
                "election_id": election_oid,
                "supersedes_id": None,
                "status": {"$ne": "superseded"},
            }
        },
        {
            "$group": {
                "_id": None,
                "total_pu": {"$sum": 1},
                "total_accredited": {"$sum": "$accredited_voters"},
                "total_cast": {"$sum": "$total_votes_cast"},
                "total_valid": {"$sum": "$valid_votes"},
                "total_rejected": {"$sum": "$rejected_votes"},
                "party_votes_list": {"$push": "$party_votes"},
            }
        },
    ]
    agg = await ElectionResult.get_pymongo_collection().aggregate(pipeline).to_list(None)
    totals = agg[0] if agg else {}

    # Tally party votes in Python (parties are typically < 10)
    party_totals: dict = defaultdict(int)
    for pv in totals.get("party_votes_list", []):
        for party, votes in (pv or {}).items():
            party_totals[party] += votes

    # Per-status counts (includes superseded for audit transparency)
    status_pipeline = [
        {"$match": {"election_id": election_oid}},
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    status_agg = await ElectionResult.get_pymongo_collection().aggregate(status_pipeline).to_list(None)
    results_by_status = {row["_id"]: row["count"] for row in status_agg}

    return ElectionSummaryRead(
        election_id=election_id,
        total_polling_units_with_results=totals.get("total_pu", 0),
        total_accredited_voters=totals.get("total_accredited", 0),
        total_votes_cast=totals.get("total_cast", 0),
        total_valid_votes=totals.get("total_valid", 0),
        total_rejected_votes=totals.get("total_rejected", 0),
        party_totals=dict(sorted(party_totals.items(), key=lambda x: -x[1])),
        results_by_status=results_by_status,
    )


@router.get("/{result_id}", response_model=ResultRead)
async def get_result(result_id: str):
    result = await ElectionResult.get(to_oid(result_id))
    if not result:
        raise not_found("ElectionResult", result_id)
    return _result_read(result)
