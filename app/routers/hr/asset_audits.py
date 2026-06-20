"""HR Asset Management — physical audits (`/hr/asset-audits`).

A reconciliation campaign over a scope (location / department / category):
DRAFT → IN_PROGRESS → COMPLETED (or CANCELLED). Starting snapshots the in-scope
assets into items; scanning records the found state; completing recomputes the
found / missing / mismatched tallies.
"""
from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.asset import Asset
from app.models.hr.asset_lifecycle import (
    AssetAudit, AssetAuditItem, AssetAuditStatus, AssetAuditResult, AssetEventType,
)
from app.schemas.hr.asset_lifecycle import (
    AssetAuditCreate, AssetAuditScanBody, AssetAuditResponse, AssetAuditListResponse,
    AssetAuditItemResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.state import assert_transition
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.responses import to_audit_item_response

router = APIRouter(prefix="/hr/asset-audits", tags=["HR — Asset Audits"])


def _load(db: Session, audit_id: UUID) -> AssetAudit:
    a = db.query(AssetAudit).filter(AssetAudit.id == audit_id, AssetAudit.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Audit not found")
    return a


def _recount(db: Session, audit: AssetAudit) -> None:
    items = db.query(AssetAuditItem).filter(AssetAuditItem.audit_id == audit.id).all()
    audit.total_expected = len(items)
    audit.total_found = sum(1 for i in items if i.result == AssetAuditResult.FOUND)
    audit.total_missing = sum(1 for i in items if i.result == AssetAuditResult.MISSING)
    audit.total_mismatched = sum(1 for i in items if i.result in (AssetAuditResult.MISMATCH, AssetAuditResult.DAMAGED))


@router.get("/", response_model=AssetAuditListResponse)
def list_audits(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    audit_status: Optional[AssetAuditStatus] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetAudit).filter(AssetAudit.is_deleted == False)  # noqa: E712
    if audit_status:
        q = q.filter(AssetAudit.status == audit_status)
    total = q.count()
    rows = q.order_by(AssetAudit.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AssetAuditListResponse(
        items=[AssetAuditResponse.model_validate(a) for a in rows],
        total=total, page=page, limit=limit, total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AssetAuditResponse, status_code=http_status.HTTP_201_CREATED)
def create_audit(
    payload: AssetAuditCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = AssetAudit(
        name=payload.name, status=AssetAuditStatus.DRAFT,
        scope_location_id=payload.scope_location_id, scope_department_id=payload.scope_department_id,
        scope_category_id=payload.scope_category_id, scheduled_date=payload.scheduled_date,
        notes=payload.notes, conducted_by_user_id=admin.id, created_by_id=admin.id,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return AssetAuditResponse.model_validate(a)


@router.get("/{audit_id}", response_model=AssetAuditResponse)
def get_audit(audit_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    return AssetAuditResponse.model_validate(_load(db, audit_id))


@router.get("/{audit_id}/items", response_model=List[AssetAuditItemResponse])
def list_audit_items(audit_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    _load(db, audit_id)
    items = db.query(AssetAuditItem).filter(AssetAuditItem.audit_id == audit_id).order_by(AssetAuditItem.created_at.asc()).all()
    return [to_audit_item_response(db, it) for it in items]


@router.post("/{audit_id}/start", response_model=AssetAuditResponse)
def start_audit(audit_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = _load(db, audit_id)
    assert_transition("audit", a.status, AssetAuditStatus.IN_PROGRESS)
    # Snapshot the in-scope assets into audit items.
    q = db.query(Asset).filter(Asset.is_deleted == False)  # noqa: E712
    if a.scope_location_id:
        q = q.filter(Asset.location_id == a.scope_location_id)
    if a.scope_department_id:
        q = q.filter(Asset.department_id == a.scope_department_id)
    if a.scope_category_id:
        q = q.filter(Asset.category_id == a.scope_category_id)
    assets = q.all()
    for asset in assets:
        db.add(AssetAuditItem(
            audit_id=a.id, asset_id=asset.id, expected_status=asset.status,
            expected_employee_id=asset.assigned_employee_id, expected_location_id=asset.location_id,
            result=AssetAuditResult.PENDING,
        ))
    a.status = AssetAuditStatus.IN_PROGRESS
    a.started_at = datetime.utcnow()
    a.total_expected = len(assets)
    db.commit()
    db.refresh(a)
    return AssetAuditResponse.model_validate(a)


@router.post("/{audit_id}/items/{item_id}/scan", response_model=AssetAuditItemResponse)
def scan_item(
    audit_id: UUID, item_id: UUID, body: AssetAuditScanBody,
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    a = _load(db, audit_id)
    if a.status != AssetAuditStatus.IN_PROGRESS:
        raise HTTPException(409, "Audit must be IN_PROGRESS to scan items.")
    it = db.query(AssetAuditItem).filter(AssetAuditItem.id == item_id, AssetAuditItem.audit_id == audit_id).first()
    if not it:
        raise HTTPException(404, "Audit item not found")
    it.result = body.result
    it.found_employee_id = body.found_employee_id
    it.found_location_id = body.found_location_id
    it.found_condition = body.found_condition
    it.remarks = body.remarks
    it.scanned_at = datetime.utcnow()
    it.scanned_by_user_id = admin.id
    db.flush()
    write_asset_history(
        db, it.asset_id, AssetEventType.AUDIT_SCANNED, actor_user_id=admin.id,
        related_entity_type="audit", related_entity_id=a.id,
        note=f"Audit '{a.name}': {body.result.value}",
    )
    _recount(db, a)
    db.commit()
    db.refresh(it)
    return to_audit_item_response(db, it)


@router.post("/{audit_id}/complete", response_model=AssetAuditResponse)
def complete_audit(audit_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = _load(db, audit_id)
    assert_transition("audit", a.status, AssetAuditStatus.COMPLETED)
    _recount(db, a)
    a.status = AssetAuditStatus.COMPLETED
    a.completed_at = datetime.utcnow()
    db.commit()
    db.refresh(a)
    return AssetAuditResponse.model_validate(a)


@router.post("/{audit_id}/cancel", response_model=AssetAuditResponse)
def cancel_audit(audit_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    a = _load(db, audit_id)
    assert_transition("audit", a.status, AssetAuditStatus.CANCELLED)
    a.status = AssetAuditStatus.CANCELLED
    db.commit()
    db.refresh(a)
    return AssetAuditResponse.model_validate(a)
