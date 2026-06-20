"""HR Asset Management — damage tickets (`/hr/asset-damages`).

Admin-facing CRUD + status workflow for damage/loss incidents. (Employees raise
their own via `/hr/me/assets/{allocation_id}/report-damage`.)
Lifecycle: REPORTED → UNDER_REVIEW → IN_REPAIR → RESOLVED / WRITE_OFF (or REJECTED).
WRITE_OFF retires the asset.
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
from app.models.hr.asset import Asset, AssetStatus
from app.models.hr.asset_lifecycle import (
    AssetDamage, AssetDamageStatus, AssetDamageSeverity, AssetEventType,
)
from app.schemas.hr.asset_lifecycle import (
    AssetDamageCreate, AssetDamageStatusUpdate, AssetDamageResolveBody,
    AssetDamageResponse, AssetDamageListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.state import assert_transition
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.responses import to_damage_response

router = APIRouter(prefix="/hr/asset-damages", tags=["HR — Asset Damage"])

_TERMINAL = (AssetDamageStatus.RESOLVED, AssetDamageStatus.WRITE_OFF, AssetDamageStatus.REJECTED)


def _load(db: Session, did: UUID) -> AssetDamage:
    d = db.query(AssetDamage).filter(AssetDamage.id == did, AssetDamage.is_deleted == False).first()  # noqa: E712
    if not d:
        raise HTTPException(404, "Damage ticket not found")
    return d


@router.get("/", response_model=AssetDamageListResponse)
def list_damages(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    asset_id: Optional[UUID] = None,
    damage_status: Optional[AssetDamageStatus] = None,
    severity: Optional[AssetDamageSeverity] = None,
    employee_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetDamage).filter(AssetDamage.is_deleted == False)  # noqa: E712
    if asset_id:
        q = q.filter(AssetDamage.asset_id == asset_id)
    if damage_status:
        q = q.filter(AssetDamage.status == damage_status)
    if severity:
        q = q.filter(AssetDamage.severity == severity)
    if employee_id:
        q = q.filter(AssetDamage.reported_by_employee_id == employee_id)
    total = q.count()
    rows = q.order_by(AssetDamage.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AssetDamageListResponse(
        items=[to_damage_response(db, d) for d in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AssetDamageResponse, status_code=http_status.HTTP_201_CREATED)
def create_damage(
    payload: AssetDamageCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    asset = db.query(Asset).filter(Asset.id == payload.asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")
    d = AssetDamage(
        asset_id=asset.id, allocation_id=payload.allocation_id,
        severity=payload.severity, status=AssetDamageStatus.REPORTED,
        reported_by_user_id=admin.id, title=payload.title,
        description=payload.description, attachments=payload.attachments or [],
        liable_employee=payload.liable_employee, recovery_amount=payload.recovery_amount,
        reported_date=date.today(),
    )
    db.add(d)
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.DAMAGE_REPORTED, actor_user_id=admin.id,
        related_entity_type="damage", related_entity_id=d.id, note=payload.title,
    )
    db.commit()
    db.refresh(d)
    return to_damage_response(db, d)


@router.get("/{damage_id}", response_model=AssetDamageResponse)
def get_damage(damage_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    return to_damage_response(db, _load(db, damage_id))


@router.patch("/{damage_id}", response_model=AssetDamageResponse)
def update_damage_status(
    damage_id: UUID,
    payload: AssetDamageStatusUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = _load(db, damage_id)
    if payload.status != d.status:
        assert_transition("damage", d.status, payload.status)
        d.status = payload.status
    if payload.liable_employee is not None:
        d.liable_employee = payload.liable_employee
    if payload.recovery_amount is not None:
        d.recovery_amount = payload.recovery_amount
    if payload.notes:
        d.resolution_notes = (d.resolution_notes or "") + ("\n" if d.resolution_notes else "") + payload.notes
    # WRITE_OFF retires the asset.
    if payload.status == AssetDamageStatus.WRITE_OFF:
        asset = db.query(Asset).filter(Asset.id == d.asset_id).first()
        if asset and asset.status != AssetStatus.RETIRED:
            prior = asset.status
            asset.status = AssetStatus.RETIRED
            asset.assigned_employee_id = None
            write_asset_history(
                db, asset.id, AssetEventType.RETIRED, actor_user_id=admin.id,
                from_status=prior, to_status=AssetStatus.RETIRED,
                related_entity_type="damage", related_entity_id=d.id,
                note="Written off (damage).",
            )
    db.commit()
    db.refresh(d)
    return to_damage_response(db, d)


@router.post("/{damage_id}/resolve", response_model=AssetDamageResponse)
def resolve_damage(
    damage_id: UUID,
    body: AssetDamageResolveBody = AssetDamageResolveBody(),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    d = _load(db, damage_id)
    target = AssetDamageStatus.WRITE_OFF if body.write_off else AssetDamageStatus.RESOLVED
    assert_transition("damage", d.status, target)
    d.status = target
    d.resolved_date = body.resolved_date or date.today()
    if body.resolution_notes:
        d.resolution_notes = body.resolution_notes
    if target == AssetDamageStatus.WRITE_OFF:
        asset = db.query(Asset).filter(Asset.id == d.asset_id).first()
        if asset and asset.status != AssetStatus.RETIRED:
            prior = asset.status
            asset.status = AssetStatus.RETIRED
            asset.assigned_employee_id = None
            write_asset_history(
                db, asset.id, AssetEventType.RETIRED, actor_user_id=admin.id,
                from_status=prior, to_status=AssetStatus.RETIRED,
                related_entity_type="damage", related_entity_id=d.id,
                note="Written off (damage).",
            )
    else:
        write_asset_history(
            db, d.asset_id, AssetEventType.DAMAGE_RESOLVED, actor_user_id=admin.id,
            related_entity_type="damage", related_entity_id=d.id,
        )
    db.commit()
    db.refresh(d)
    return to_damage_response(db, d)


@router.post("/{damage_id}/reject", response_model=AssetDamageResponse)
def reject_damage(
    damage_id: UUID,
    body: AssetDamageResolveBody = AssetDamageResolveBody(),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    d = _load(db, damage_id)
    assert_transition("damage", d.status, AssetDamageStatus.REJECTED)
    d.status = AssetDamageStatus.REJECTED
    if body.resolution_notes:
        d.resolution_notes = body.resolution_notes
    db.commit()
    db.refresh(d)
    return to_damage_response(db, d)
