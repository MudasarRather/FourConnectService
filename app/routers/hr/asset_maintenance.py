"""HR Asset Management — maintenance & repairs (`/hr/asset-maintenance`).

Lifecycle: SCHEDULED → IN_PROGRESS → COMPLETED (or CANCELLED). Starting a job moves
the asset to MAINTENANCE (snapshotting its prior status); completing restores the
prior status unless the post-repair condition is RETIRED.
"""
from __future__ import annotations

from datetime import date
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.asset import Asset, AssetStatus, AssetCondition
from app.models.hr.asset_lifecycle import (
    AssetMaintenance, AssetMaintenanceStatus, AssetEventType,
)
from app.schemas.hr.asset_lifecycle import (
    AssetMaintenanceCreate, AssetMaintenanceUpdate, AssetMaintenanceCompleteBody,
    AssetMaintenanceResponse, AssetMaintenanceListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.state import assert_transition
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.responses import to_maintenance_response

router = APIRouter(prefix="/hr/asset-maintenance", tags=["HR — Asset Maintenance"])


def _load(db: Session, mid: UUID) -> AssetMaintenance:
    m = db.query(AssetMaintenance).filter(AssetMaintenance.id == mid, AssetMaintenance.is_deleted == False).first()  # noqa: E712
    if not m:
        raise HTTPException(404, "Maintenance record not found")
    return m


@router.get("/", response_model=AssetMaintenanceListResponse)
def list_maintenance(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    asset_id: Optional[UUID] = None,
    maintenance_status: Optional[AssetMaintenanceStatus] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetMaintenance).filter(AssetMaintenance.is_deleted == False)  # noqa: E712
    if asset_id:
        q = q.filter(AssetMaintenance.asset_id == asset_id)
    if maintenance_status:
        q = q.filter(AssetMaintenance.status == maintenance_status)
    total = q.count()
    rows = q.order_by(AssetMaintenance.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AssetMaintenanceListResponse(
        items=[to_maintenance_response(db, m) for m in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AssetMaintenanceResponse, status_code=http_status.HTTP_201_CREATED)
def schedule_maintenance(
    payload: AssetMaintenanceCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    asset = db.query(Asset).filter(Asset.id == payload.asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")
    if asset.status == AssetStatus.RETIRED:
        raise HTTPException(409, "Asset is RETIRED; cannot schedule maintenance.")
    m = AssetMaintenance(
        asset_id=asset.id,
        maintenance_type=payload.maintenance_type,
        status=AssetMaintenanceStatus.SCHEDULED,
        vendor_id=payload.vendor_id,
        damage_id=payload.damage_id,
        reported_by_user_id=admin.id,
        reported_date=date.today(),
        scheduled_date=payload.scheduled_date,
        cost=payload.cost,
        description=payload.description,
        condition_before=asset.condition,
        attachments=payload.attachments or [],
    )
    db.add(m)
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.MAINTENANCE_SCHEDULED, actor_user_id=admin.id,
        related_entity_type="maintenance", related_entity_id=m.id,
        note=payload.description,
    )
    db.commit()
    db.refresh(m)
    return to_maintenance_response(db, m)


@router.get("/{maintenance_id}", response_model=AssetMaintenanceResponse)
def get_maintenance(maintenance_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    return to_maintenance_response(db, _load(db, maintenance_id))


@router.patch("/{maintenance_id}", response_model=AssetMaintenanceResponse)
def update_maintenance(
    maintenance_id: UUID,
    payload: AssetMaintenanceUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    m = _load(db, maintenance_id)
    if m.status in (AssetMaintenanceStatus.COMPLETED, AssetMaintenanceStatus.CANCELLED):
        raise HTTPException(409, f"Maintenance is {m.status.value}; cannot edit.")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    return to_maintenance_response(db, m)


@router.post("/{maintenance_id}/start", response_model=AssetMaintenanceResponse)
def start_maintenance(
    maintenance_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    m = _load(db, maintenance_id)
    assert_transition("maintenance", m.status, AssetMaintenanceStatus.IN_PROGRESS)
    asset = db.query(Asset).filter(Asset.id == m.asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")
    if asset.status == AssetStatus.RETIRED:
        raise HTTPException(409, "Asset is RETIRED.")
    prior = asset.status
    m.prior_status = prior.value
    m.status = AssetMaintenanceStatus.IN_PROGRESS
    m.started_date = date.today()
    asset.status = AssetStatus.MAINTENANCE
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.MAINTENANCE_STARTED, actor_user_id=admin.id,
        from_status=prior, to_status=asset.status,
        related_entity_type="maintenance", related_entity_id=m.id,
    )
    db.commit()
    db.refresh(m)
    return to_maintenance_response(db, m)


@router.post("/{maintenance_id}/complete", response_model=AssetMaintenanceResponse)
def complete_maintenance(
    maintenance_id: UUID,
    body: AssetMaintenanceCompleteBody = AssetMaintenanceCompleteBody(),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    m = _load(db, maintenance_id)
    assert_transition("maintenance", m.status, AssetMaintenanceStatus.COMPLETED)
    asset = db.query(Asset).filter(Asset.id == m.asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")
    prior = asset.status
    m.status = AssetMaintenanceStatus.COMPLETED
    m.completed_date = body.completed_date or date.today()
    if body.cost is not None:
        m.cost = body.cost
    if body.condition_after is not None:
        m.condition_after = body.condition_after
        asset.condition = body.condition_after
    if body.resolution_notes:
        m.resolution_notes = body.resolution_notes

    # Restore prior status (AVAILABLE / ALLOCATED) unless the repair retired it.
    if m.condition_after == AssetCondition.RETIRED:
        asset.status = AssetStatus.RETIRED
        asset.assigned_employee_id = None
    else:
        restore = AssetStatus.AVAILABLE
        if m.prior_status:
            try:
                restore = AssetStatus(m.prior_status)
            except ValueError:
                restore = AssetStatus.AVAILABLE
        # An asset can't return to ALLOCATED if it's no longer assigned.
        if restore == AssetStatus.ALLOCATED and not asset.assigned_employee_id:
            restore = AssetStatus.AVAILABLE
        asset.status = restore
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.MAINTENANCE_COMPLETED, actor_user_id=admin.id,
        from_status=prior, to_status=asset.status,
        related_entity_type="maintenance", related_entity_id=m.id,
    )
    db.commit()
    db.refresh(m)
    return to_maintenance_response(db, m)


@router.post("/{maintenance_id}/cancel", response_model=AssetMaintenanceResponse)
def cancel_maintenance(
    maintenance_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    m = _load(db, maintenance_id)
    assert_transition("maintenance", m.status, AssetMaintenanceStatus.CANCELLED)
    was_in_progress = m.status == AssetMaintenanceStatus.IN_PROGRESS
    m.status = AssetMaintenanceStatus.CANCELLED
    # If we'd already taken the asset down for maintenance, give it back.
    if was_in_progress:
        asset = db.query(Asset).filter(Asset.id == m.asset_id, Asset.is_deleted == False).first()  # noqa: E712
        if asset and asset.status == AssetStatus.MAINTENANCE:
            restore = AssetStatus.AVAILABLE
            if m.prior_status:
                try:
                    restore = AssetStatus(m.prior_status)
                except ValueError:
                    restore = AssetStatus.AVAILABLE
            if restore == AssetStatus.ALLOCATED and not asset.assigned_employee_id:
                restore = AssetStatus.AVAILABLE
            asset.status = restore
    db.commit()
    db.refresh(m)
    return to_maintenance_response(db, m)
