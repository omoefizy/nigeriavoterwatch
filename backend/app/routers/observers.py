"""
Observer management endpoints.

POST /api/observers/invite        Admin: pre-create a pending slot + return invite code
POST /api/observers/register      Observer: complete registration with invite code + password
GET  /api/observers/reports       List field reports (filter by election / PU) — public
POST /api/observers/reports       Submit a new field report — requires observer or admin JWT
GET  /api/observers/reports/{id}  Single report detail — public
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query, Request
from passlib.context import CryptContext

from app.auth.jwt import require_admin, require_observer_or_admin
from app.models.audit import AuditLog, AuditAction
from app.models.elections import Election, PollingUnit
from app.models.observers import Observer, ObserverReport, ObserverStatus
from app.models.user import User, UserRole
from app.schemas.common import PaginatedResponse, make_pages, not_found, to_oid
from app.schemas.observers import (
    ObserverInvite, ObserverRead, ObserverRegister,
    ReportRead, ReportSubmit,
)
from app.config import settings

router = APIRouter()
log = structlog.get_logger()
_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _observer_read(o: Observer) -> ObserverRead:
    return ObserverRead(
        id=str(o.id),
        full_name=o.full_name,
        email=o.email,
        phone=o.phone,
        organization=o.organization,
        accreditation_id=o.accreditation_id,
        status=o.status,
        assigned_polling_unit_id=(
            str(o.assigned_polling_unit_id) if o.assigned_polling_unit_id else None
        ),
        created_at=o.created_at,
    )


def _report_read(r: ObserverReport) -> ReportRead:
    return ReportRead(
        id=str(r.id),
        observer_id=str(r.observer_id),
        election_id=str(r.election_id),
        polling_unit_id=str(r.polling_unit_id),
        accreditation_started_at=r.accreditation_started_at,
        voting_started_at=r.voting_started_at,
        voting_ended_at=r.voting_ended_at,
        estimated_turnout=r.estimated_turnout,
        violence_reported=r.violence_reported,
        ballot_stuffing_reported=r.ballot_stuffing_reported,
        underage_voting_reported=r.underage_voting_reported,
        security_personnel_present=r.security_personnel_present,
        inec_officials_present=r.inec_officials_present,
        narrative=r.narrative,
        photo_paths=r.photo_paths,
        location=r.location,
        is_reviewed=r.is_reviewed,
        created_at=r.created_at,
    )


# ── Invite (admin only) ───────────────────────────────────────────────────────

@router.post("/invite", status_code=201)
async def create_invite(body: ObserverInvite, _=Depends(require_admin)):
    """
    Admin-only: pre-create a pending Observer document and return a one-time
    invite code. The code is never stored in plaintext — only its SHA-256 hash
    is persisted. Deliver the raw code to the observer out-of-band (email/SMS).
    """
    existing = await Observer.find_one(Observer.email == body.email)
    if existing and existing.status != ObserverStatus.PENDING:
        from fastapi import HTTPException
        raise HTTPException(400, "An active observer already exists for this email")

    raw_code = secrets.token_urlsafe(24)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=settings.observer_invite_code_ttl_hours)

    pu_oid = to_oid(body.assigned_polling_unit_id) if body.assigned_polling_unit_id else None

    if existing:
        existing.full_name = body.full_name
        existing.invite_code_hash = _hash_code(raw_code)
        existing.invite_code_expires_at = expires_at
        existing.assigned_polling_unit_id = pu_oid
        await existing.save()
    else:
        await Observer(
            full_name=body.full_name,
            email=body.email,
            status=ObserverStatus.PENDING,
            invite_code_hash=_hash_code(raw_code),
            invite_code_expires_at=expires_at,
            assigned_polling_unit_id=pu_oid,
        ).insert()

    log.info("Observer invite created", email=body.email, expires=expires_at.isoformat())
    return {
        "message": "Invite created. Deliver the invite_code to the observer securely.",
        "email": body.email,
        "invite_code": raw_code,
        "expires_at": expires_at.isoformat(),
    }


# ── Registration (public — observers self-register with invite code) ───────────

@router.post("/register", response_model=ObserverRead, status_code=201)
async def register_observer(body: ObserverRegister, request: Request):
    observer = await Observer.find_one(Observer.email == body.email)
    if not observer:
        raise not_found("Pending invitation", body.email)

    if observer.status != ObserverStatus.PENDING:
        from fastapi import HTTPException
        raise HTTPException(400, "Observer is already registered or suspended")

    if observer.invite_code_hash != _hash_code(body.invite_code):
        from fastapi import HTTPException
        raise HTTPException(400, "Invalid invite code")

    expires = observer.invite_code_expires_at
    if expires and expires.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
        from fastapi import HTTPException
        raise HTTPException(400, "Invite code has expired")

    # Complete Observer registration
    observer.full_name = body.full_name
    observer.phone = body.phone
    observer.organization = body.organization
    observer.accreditation_id = body.accreditation_id
    observer.status = ObserverStatus.VERIFIED
    observer.invite_code_hash = None
    observer.invite_code_expires_at = None
    observer.last_active_at = datetime.now(timezone.utc)
    await observer.save()

    # Create (or update) the User account for JWT auth
    existing_user = await User.find_one(User.email == body.email.lower())
    if existing_user:
        existing_user.password_hash = _pwd.hash(body.password)
        existing_user.observer_id = str(observer.id)
        existing_user.is_active = True
        await existing_user.save()
    else:
        await User(
            email=body.email.lower(),
            password_hash=_pwd.hash(body.password),
            role=UserRole.observer,
            full_name=body.full_name,
            observer_id=str(observer.id),
        ).insert()

    await AuditLog(
        action=AuditAction.OBSERVER_REGISTERED,
        actor_type="observer",
        actor_id=str(observer.id),
        entity_type="observer",
        entity_id=str(observer.id),
        ip_address=request.client.host if request.client else None,
        details={"email": observer.email, "organization": observer.organization},
    ).insert()

    log.info("Observer registered", id=str(observer.id), email=observer.email)
    return _observer_read(observer)


# ── Reports ───────────────────────────────────────────────────────────────────

@router.post("/reports", response_model=ReportRead, status_code=201)
async def submit_report(
    body: ReportSubmit,
    request: Request,
    current_user=Depends(require_observer_or_admin),
):
    election_oid = to_oid(body.election_id)
    pu_oid = to_oid(body.polling_unit_id)

    if not await Election.get(election_oid):
        raise not_found("Election", body.election_id)
    if not await PollingUnit.get(pu_oid):
        raise not_found("PollingUnit", body.polling_unit_id)

    # Resolve observer document from JWT user
    if current_user.observer_id:
        observer_oid = to_oid(current_user.observer_id)
        observer = await Observer.get(observer_oid)
    else:
        # Admin submitting on behalf — look up by email
        observer = await Observer.find_one(Observer.email == current_user.email)

    if not observer or observer.status != ObserverStatus.VERIFIED:
        from fastapi import HTTPException
        raise HTTPException(403, "No verified observer profile linked to this account")

    report = ObserverReport(
        observer_id=observer.id,
        election_id=election_oid,
        polling_unit_id=pu_oid,
        accreditation_started_at=body.accreditation_started_at,
        voting_started_at=body.voting_started_at,
        voting_ended_at=body.voting_ended_at,
        estimated_turnout=body.estimated_turnout,
        violence_reported=body.violence_reported,
        ballot_stuffing_reported=body.ballot_stuffing_reported,
        underage_voting_reported=body.underage_voting_reported,
        security_personnel_present=body.security_personnel_present,
        inec_officials_present=body.inec_officials_present,
        narrative=body.narrative,
        photo_paths=body.photo_paths,
        location=body.location.to_embedded() if body.location else None,
    )
    await report.insert()

    observer.last_active_at = datetime.now(timezone.utc)
    await observer.save()

    await AuditLog(
        action=AuditAction.OBSERVER_REPORT_SUBMITTED,
        actor_type="observer",
        actor_id=str(observer.id),
        entity_type="observer_report",
        entity_id=str(report.id),
        ip_address=request.client.host if request.client else None,
        details={
            "election_id": body.election_id,
            "polling_unit_id": body.polling_unit_id,
            "violence_reported": body.violence_reported,
            "ballot_stuffing_reported": body.ballot_stuffing_reported,
        },
    ).insert()

    log.info("Report submitted", report_id=str(report.id), pu=body.polling_unit_id)
    return _report_read(report)


@router.get("/reports", response_model=PaginatedResponse[ReportRead])
async def list_reports(
    election_id: Optional[str] = None,
    polling_unit_id: Optional[str] = None,
    observer_id: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    raw: dict = {}
    if election_id:
        raw["election_id"] = to_oid(election_id)
    if polling_unit_id:
        raw["polling_unit_id"] = to_oid(polling_unit_id)
    if observer_id:
        raw["observer_id"] = to_oid(observer_id)

    q = ObserverReport.find(raw)
    total = await q.count()
    items = await q.sort("-created_at").skip((page - 1) * page_size).limit(page_size).to_list()

    return PaginatedResponse(
        items=[_report_read(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=make_pages(total, page_size),
    )


@router.get("/reports/{report_id}", response_model=ReportRead)
async def get_report(report_id: str):
    report = await ObserverReport.get(to_oid(report_id))
    if not report:
        raise not_found("ObserverReport", report_id)
    return _report_read(report)
