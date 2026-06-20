"""HR Asset Management — disposal (`/hr/asset-disposals`).

End-of-life workflow: REQUESTED → APPROVED → COMPLETED (or REJECTED / CANCELLED).
Guards: an ALLOCATED asset must be returned first; only one active disposal per
asset; completing retires the asset.
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
    AssetDisposal, AssetDisposalStatus, AssetEventType,
)
from app.schemas.hr.asset_lifecycle import (
    AssetDisposalCreate, AssetDisposalDecisionBody, AssetDisposalResponse, AssetDisposalListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.state import assert_transition
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.responses import to_disposal_response

router = APIRouter(prefix="/hr/asset-disposals", tags=["HR — Asset Disposal"])

_ACTIVE = (AssetDisposalStatus.REQUESTED, AssetDisposalStatus.APPROVED)


def _load(db: Session, disposal_id: UUID) -> AssetDisposal:
    d = db.query(AssetDisposal).filter(AssetDisposal.id == disposal_id, AssetDisposal.is_deleted == False).first()  # noqa: E712
    if not d:
        raise HTTPException(404, "Disposal not found")
    return d


@router.get("/", response_model=AssetDisposalListResponse)
def list_disposals(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    disposal_status: Optional[AssetDisposalStatus] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetDisposal).filter(AssetDisposal.is_deleted == False)  # noqa: E712
    if disposal_status:
        q = q.filter(AssetDisposal.status == disposal_status)
    total = q.count()
    rows = q.order_by(AssetDisposal.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AssetDisposalListResponse(
        items=[to_disposal_response(db, d) for d in rows],
        total=total, page=page, limit=limit, total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AssetDisposalResponse, status_code=http_status.HTTP_201_CREATED)
def request_disposal(
    payload: AssetDisposalCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    asset = db.query(Asset).filter(Asset.id == payload.asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")
    if asset.status == AssetStatus.ALLOCATED:
        raise HTTPException(409, "Asset is allocated; return it before disposal.")
    dup = db.query(AssetDisposal.id).filter(
        AssetDisposal.asset_id == asset.id, AssetDisposal.status.in_(_ACTIVE),
        AssetDisposal.is_deleted == False,  # noqa: E712
    ).first()
    if dup:
        raise HTTPException(409, "An active disposal already exists for this asset.")
    d = AssetDisposal(
        asset_id=asset.id, disposal_method=payload.disposal_method, status=AssetDisposalStatus.REQUESTED,
        reason=payload.reason, sale_value=payload.sale_value, buyer=payload.buyer,
        book_value=asset.current_book_value if asset.current_book_value is not None else asset.purchase_cost,
        attachments=payload.attachments or [], notes=payload.notes,
        requested_by_user_id=admin.id, request_date=date.today(),
    )
    db.add(d)
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.DISPOSAL_REQUESTED, actor_user_id=admin.id,
        related_entity_type="disposal", related_entity_id=d.id, note=payload.reason,
    )
    db.commit()
    db.refresh(d)
    return to_disposal_response(db, d)


@router.get("/{disposal_id}", response_model=AssetDisposalResponse)
def get_disposal(disposal_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    return to_disposal_response(db, _load(db, disposal_id))


@router.post("/{disposal_id}/approve", response_model=AssetDisposalResponse)
def approve_disposal(
    disposal_id: UUID, body: AssetDisposalDecisionBody = AssetDisposalDecisionBody(),
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _load(db, disposal_id)
    assert_transition("disposal", d.status, AssetDisposalStatus.APPROVED)
    d.status = AssetDisposalStatus.APPROVED
    d.approved_by_user_id = admin.id
    d.approved_date = date.today()
    if body.notes:
        d.notes = (d.notes or "") + ("\n" if d.notes else "") + body.notes
    db.flush()
    write_asset_history(
        db, d.asset_id, AssetEventType.DISPOSAL_APPROVED, actor_user_id=admin.id,
        related_entity_type="disposal", related_entity_id=d.id,
    )
    db.commit()
    db.refresh(d)
    return to_disposal_response(db, d)


@router.post("/{disposal_id}/reject", response_model=AssetDisposalResponse)
def reject_disposal(
    disposal_id: UUID, body: AssetDisposalDecisionBody = AssetDisposalDecisionBody(),
    db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser),
):
    d = _load(db, disposal_id)
    assert_transition("disposal", d.status, AssetDisposalStatus.REJECTED)
    d.status = AssetDisposalStatus.REJECTED
    if body.notes:
        d.notes = (d.notes or "") + ("\n" if d.notes else "") + body.notes
    db.commit()
    db.refresh(d)
    return to_disposal_response(db, d)


@router.post("/{disposal_id}/cancel", response_model=AssetDisposalResponse)
def cancel_disposal(
    disposal_id: UUID, body: AssetDisposalDecisionBody = AssetDisposalDecisionBody(),
    db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser),
):
    d = _load(db, disposal_id)
    assert_transition("disposal", d.status, AssetDisposalStatus.CANCELLED)
    d.status = AssetDisposalStatus.CANCELLED
    if body.notes:
        d.notes = (d.notes or "") + ("\n" if d.notes else "") + body.notes
    db.commit()
    db.refresh(d)
    return to_disposal_response(db, d)


@router.post("/{disposal_id}/complete", response_model=AssetDisposalResponse)
def complete_disposal(
    disposal_id: UUID, body: AssetDisposalDecisionBody = AssetDisposalDecisionBody(),
    db: Session = Depends(get_db), admin: User = Depends(get_current_superuser),
):
    d = _load(db, disposal_id)
    assert_transition("disposal", d.status, AssetDisposalStatus.COMPLETED)
    asset = db.query(Asset).filter(Asset.id == d.asset_id).first()
    d.status = AssetDisposalStatus.COMPLETED
    d.disposed_date = body.disposed_date or date.today()
    if body.sale_value is not None:
        d.sale_value = body.sale_value
    if body.notes:
        d.notes = (d.notes or "") + ("\n" if d.notes else "") + body.notes
    if asset and asset.status != AssetStatus.RETIRED:
        prior = asset.status
        asset.status = AssetStatus.RETIRED
        asset.assigned_employee_id = None
        db.flush()
        write_asset_history(
            db, asset.id, AssetEventType.DISPOSAL_COMPLETED, actor_user_id=admin.id,
            from_status=prior, to_status=AssetStatus.RETIRED,
            related_entity_type="disposal", related_entity_id=d.id,
            note=f"Disposed: {d.disposal_method.value}",
        )
    db.commit()
    db.refresh(d)
    return to_disposal_response(db, d)
