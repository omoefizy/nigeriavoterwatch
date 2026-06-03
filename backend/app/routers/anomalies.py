"""
Anomaly management endpoints.

GET   /api/anomalies                       List (filter by election, severity, type, resolved)
GET   /api/anomalies/{anomaly_id}          Single anomaly detail
PATCH /api/anomalies/{anomaly_id}/resolve  Mark resolved
"""
from datetime import datetime, timezone
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query

from app.models.audit import AuditLog, AuditAction
from app.models.observers import Anomaly, AnomalyType, AnomalySeverity
from app.schemas.anomalies import AnomalyRead, AnomalyResolve
from app.schemas.common import PaginatedResponse, make_pages, not_found, to_oid
from app.auth.jwt import require_admin

router = APIRouter()
log = structlog.get_logger()


def _anomaly_read(a: Anomaly) -> AnomalyRead:
    return AnomalyRead(
        id=str(a.id),
        election_id=str(a.election_id),
        polling_unit_id=str(a.polling_unit_id) if a.polling_unit_id else None,
        anomaly_type=a.anomaly_type,
        severity=a.severity,
        description=a.description,
        details=a.details,
        state_name=a.state_name,
        lga_name=a.lga_name,
        ward_name=a.ward_name,
        source_result_id=str(a.source_result_id) if a.source_result_id else None,
        source_report_id=str(a.source_report_id) if a.source_report_id else None,
        is_resolved=a.is_resolved,
        resolution_notes=a.resolution_notes,
        resolved_by=a.resolved_by,
        resolved_at=a.resolved_at,
        detected_at=a.detected_at,
        created_at=a.created_at,
    )


@router.get("", response_model=PaginatedResponse[AnomalyRead])
async def list_anomalies(
    election_id: Optional[str] = None,
    polling_unit_id: Optional[str] = None,
    state_name: Optional[str] = None,
    severity: Optional[AnomalySeverity] = None,
    anomaly_type: Optional[AnomalyType] = None,
    is_resolved: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    raw: dict = {}
    if election_id:
        raw["election_id"] = to_oid(election_id)
    if polling_unit_id:
        raw["polling_unit_id"] = to_oid(polling_unit_id)
    if state_name:
        raw["state_name"] = state_name
    if severity:
        raw["severity"] = severity.value
    if anomaly_type:
        raw["anomaly_type"] = anomaly_type.value
    if is_resolved is not None:
        raw["is_resolved"] = is_resolved

    q = Anomaly.find(raw)
    total = await q.count()
    # Unresolved critical/warning first, then by detection time desc
    items = (
        await q
        .sort(["+is_resolved", "+severity", "-detected_at"])
        .skip((page - 1) * page_size)
        .limit(page_size)
        .to_list()
    )

    return PaginatedResponse(
        items=[_anomaly_read(a) for a in items],
        total=total,
        page=page,
        page_size=page_size,
        pages=make_pages(total, page_size),
    )


@router.get("/{anomaly_id}", response_model=AnomalyRead)
async def get_anomaly(anomaly_id: str):
    anomaly = await Anomaly.get(to_oid(anomaly_id))
    if not anomaly:
        raise not_found("Anomaly", anomaly_id)
    return _anomaly_read(anomaly)


@router.patch("/{anomaly_id}/resolve", response_model=AnomalyRead)
async def resolve_anomaly(anomaly_id: str, body: AnomalyResolve, _=Depends(require_admin)):
    anomaly = await Anomaly.get(to_oid(anomaly_id))
    if not anomaly:
        raise not_found("Anomaly", anomaly_id)

    if anomaly.is_resolved:
        from fastapi import HTTPException
        raise HTTPException(400, "Anomaly is already resolved")

    anomaly.is_resolved = True
    anomaly.resolution_notes = body.resolution_notes
    anomaly.resolved_by = body.resolved_by
    anomaly.resolved_at = datetime.now(timezone.utc)
    await anomaly.save()

    await AuditLog(
        action=AuditAction.ANOMALY_RESOLVED,
        actor_type="api",
        actor_id=body.resolved_by,
        entity_type="anomaly",
        entity_id=anomaly_id,
        details={
            "anomaly_type": anomaly.anomaly_type,
            "severity": anomaly.severity,
            "resolution_notes": body.resolution_notes,
        },
    ).insert()

    log.info("Anomaly resolved", id=anomaly_id, by=body.resolved_by)
    return _anomaly_read(anomaly)
