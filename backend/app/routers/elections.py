"""
Elections CRUD + Nigerian geographic hierarchy endpoints.

/api/elections        — election lifecycle management
/api/elections/geography/...  — state → LGA → ward → polling-unit traversal
"""
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query
from beanie import PydanticObjectId

from app.models.audit import AuditLog, AuditAction
from app.models.elections import (
    Election, ElectionStatus, ElectionType,
    State, LGA, Ward, PollingUnit,
)
from app.schemas.common import PaginatedResponse, make_pages, not_found, to_oid
from app.schemas.elections import (
    ElectionCreate, ElectionRead, ElectionStatusUpdate,
    LGARead, PollingUnitRead, StateRead, WardRead,
)
from app.auth.jwt import require_admin

router = APIRouter()
log = structlog.get_logger()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _election_read(e: Election) -> ElectionRead:
    return ElectionRead(
        id=str(e.id),
        name=e.name,
        election_type=e.election_type,
        status=e.status,
        election_date=e.election_date,
        scope_state_id=str(e.scope_state_id) if e.scope_state_id else None,
        created_at=e.created_at,
        updated_at=e.updated_at,
    )


# ── Elections ─────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[ElectionRead])
async def list_elections(
    status: Optional[ElectionStatus] = None,
    election_type: Optional[ElectionType] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    conditions = []
    if status:
        conditions.append(Election.status == status)
    if election_type:
        conditions.append(Election.election_type == election_type)

    q = Election.find(*conditions)
    total = await q.count()
    items = await q.sort("-election_date").skip((page - 1) * page_size).limit(page_size).to_list()

    return PaginatedResponse(
        items=[_election_read(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=make_pages(total, page_size),
    )


@router.post("", response_model=ElectionRead, status_code=201)
async def create_election(body: ElectionCreate, _=Depends(require_admin)):
    scope_oid: Optional[PydanticObjectId] = None
    if body.scope_state_id:
        scope_oid = to_oid(body.scope_state_id)
        if not await State.get(scope_oid):
            raise not_found("State", body.scope_state_id)

    election = Election(
        name=body.name,
        election_type=body.election_type,
        election_date=body.election_date,
        scope_state_id=scope_oid,
    )
    await election.insert()

    await AuditLog(
        action=AuditAction.ELECTION_CREATED,
        actor_type="api",
        entity_type="election",
        entity_id=str(election.id),
        details={"name": election.name, "type": election.election_type},
    ).insert()

    log.info("Election created", id=str(election.id), name=election.name)
    return _election_read(election)


@router.get("/{election_id}", response_model=ElectionRead)
async def get_election(election_id: str):
    election = await Election.get(to_oid(election_id))
    if not election:
        raise not_found("Election", election_id)
    return _election_read(election)


@router.patch("/{election_id}/status", response_model=ElectionRead)
async def update_election_status(election_id: str, body: ElectionStatusUpdate, _=Depends(require_admin)):
    election = await Election.get(to_oid(election_id))
    if not election:
        raise not_found("Election", election_id)

    old_status = election.status
    election.status = body.status
    election.updated_at = datetime.now(timezone.utc)
    await election.save()

    await AuditLog(
        action=AuditAction.ELECTION_STATUS_CHANGED,
        actor_type="api",
        entity_type="election",
        entity_id=str(election.id),
        details={"from": old_status, "to": body.status},
    ).insert()

    log.info("Election status updated", id=election_id, status=body.status)
    return _election_read(election)


# ── Geographic hierarchy ───────────────────────────────────────────────────────

@router.get("/geography/states", response_model=list[StateRead])
async def list_states():
    states = await State.find_all().sort("+name").to_list()
    return [
        StateRead(
            id=str(s.id),
            name=s.name,
            code=s.code,
            geopolitical_zone=s.geopolitical_zone,
            centroid_lat=s.centroid_lat,
            centroid_lng=s.centroid_lng,
        )
        for s in states
    ]


@router.get("/geography/states/{state_id}/lgas", response_model=list[LGARead])
async def list_lgas(state_id: str):
    state_oid = to_oid(state_id)
    if not await State.get(state_oid):
        raise not_found("State", state_id)
    lgas = await LGA.find(LGA.state_id == state_oid).sort("+name").to_list()
    return [
        LGARead(id=str(l.id), name=l.name, state_id=str(l.state_id), inec_lga_code=l.inec_lga_code)
        for l in lgas
    ]


@router.get("/geography/lgas/{lga_id}/wards", response_model=list[WardRead])
async def list_wards(lga_id: str):
    lga_oid = to_oid(lga_id)
    if not await LGA.get(lga_oid):
        raise not_found("LGA", lga_id)
    wards = await Ward.find(Ward.lga_id == lga_oid).sort("+name").to_list()
    return [
        WardRead(id=str(w.id), name=w.name, lga_id=str(w.lga_id), inec_ward_code=w.inec_ward_code)
        for w in wards
    ]


@router.get("/geography/wards/{ward_id}/polling-units", response_model=list[PollingUnitRead])
async def list_polling_units(ward_id: str):
    ward_oid = to_oid(ward_id)
    if not await Ward.get(ward_oid):
        raise not_found("Ward", ward_id)
    pus = await PollingUnit.find(PollingUnit.ward_id == ward_oid).sort("+name").to_list()
    return [
        PollingUnitRead(
            id=str(p.id),
            name=p.name,
            inec_pu_code=p.inec_pu_code,
            ward_id=str(p.ward_id),
            registered_voters=p.registered_voters,
            latitude=p.latitude,
            longitude=p.longitude,
            is_active=p.is_active,
        )
        for p in pus
    ]


@router.get("/geography/polling-units/by-code/{pu_code}", response_model=PollingUnitRead)
async def get_polling_unit_by_code(pu_code: str):
    pu = await PollingUnit.find_one(PollingUnit.inec_pu_code == pu_code)
    if not pu:
        raise not_found("PollingUnit", pu_code)
    return PollingUnitRead(
        id=str(pu.id),
        name=pu.name,
        inec_pu_code=pu.inec_pu_code,
        ward_id=str(pu.ward_id),
        registered_voters=pu.registered_voters,
        latitude=pu.latitude,
        longitude=pu.longitude,
        is_active=pu.is_active,
    )
