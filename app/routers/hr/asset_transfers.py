"""HR Asset Management — transfers (`/hr/asset-transfers`).

A transfer moves an asset between employees / locations / departments / the store.
Lifecycle: REQUESTED → APPROVED → COMPLETED (or REJECTED / CANCELLED). Completing
an employee→employee transfer atomically closes the old allocation and opens a new
one so the asset never leaves an inconsistent state.
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
from app.models.hr.employee import Employee
from app.models.hr.asset import Asset, AssetAllocation, AssetStatus, AllocationStatus
from app.models.hr.asset_lifecycle import (
    AssetTransfer, AssetTransferType, AssetTransferStatus, AssetEventType,
)
from app.schemas.hr.asset_lifecycle import (
    AssetTransferCreate, AssetTransferDecisionBody, AssetTransferResponse, AssetTransferListResponse,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.assets.state import assert_transition
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.responses import to_transfer_response

router = APIRouter(prefix="/hr/asset-transfers", tags=["HR — Asset Transfers"])

_TO_EMPLOYEE = (AssetTransferType.EMPLOYEE_TO_EMPLOYEE, AssetTransferType.STORE_TO_EMPLOYEE)
_FROM_EMPLOYEE = (AssetTransferType.EMPLOYEE_TO_EMPLOYEE, AssetTransferType.EMPLOYEE_TO_STORE)


def _load(db: Session, transfer_id: UUID) -> AssetTransfer:
    t = db.query(AssetTransfer).filter(AssetTransfer.id == transfer_id, AssetTransfer.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Transfer not found")
    return t


@router.get("/", response_model=AssetTransferListResponse)
def list_transfers(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    asset_id: Optional[UUID] = None,
    transfer_status: Optional[AssetTransferStatus] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AssetTransfer).filter(AssetTransfer.is_deleted == False)  # noqa: E712
    if asset_id:
        q = q.filter(AssetTransfer.asset_id == asset_id)
    if transfer_status:
        q = q.filter(AssetTransfer.status == transfer_status)
    total = q.count()
    rows = q.order_by(AssetTransfer.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AssetTransferListResponse(
        items=[to_transfer_response(db, t) for t in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AssetTransferResponse, status_code=http_status.HTTP_201_CREATED)
def request_transfer(
    payload: AssetTransferCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    asset = db.query(Asset).filter(Asset.id == payload.asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")

    # Validate eligibility based on transfer type.
    if payload.transfer_type in _FROM_EMPLOYEE:
        if asset.status != AssetStatus.ALLOCATED:
            raise HTTPException(409, "Asset is not currently allocated; cannot transfer from an employee.")
    if payload.transfer_type == AssetTransferType.STORE_TO_EMPLOYEE:
        if asset.status != AssetStatus.AVAILABLE:
            raise HTTPException(409, "Asset must be AVAILABLE to issue from the store.")
    if payload.transfer_type in _TO_EMPLOYEE and not payload.to_employee_id:
        raise HTTPException(400, "to_employee_id is required for this transfer type.")
    if payload.to_employee_id and not db.query(Employee).filter(Employee.id == payload.to_employee_id).first():
        raise HTTPException(404, "Destination employee not found")

    # Find the current open allocation (the 'from' side) if any.
    open_alloc = db.query(AssetAllocation).filter(
        AssetAllocation.asset_id == asset.id,
        AssetAllocation.status == AllocationStatus.ALLOCATED,
    ).order_by(AssetAllocation.created_at.desc()).first()

    t = AssetTransfer(
        asset_id=asset.id,
        transfer_type=payload.transfer_type,
        status=AssetTransferStatus.REQUESTED,
        from_employee_id=(open_alloc.employee_id if open_alloc else None),
        to_employee_id=payload.to_employee_id,
        from_location_id=asset.location_id,
        to_location_id=payload.to_location_id,
        from_department_id=asset.department_id,
        to_department_id=payload.to_department_id,
        reason=payload.reason,
        effective_date=payload.effective_date,
        old_allocation_id=(open_alloc.id if open_alloc else None),
        requested_by_user_id=admin.id,
        notes=payload.notes,
    )
    db.add(t)
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.TRANSFER_REQUESTED, actor_user_id=admin.id,
        related_entity_type="transfer", related_entity_id=t.id, note=payload.reason,
    )
    db.commit()
    db.refresh(t)
    return to_transfer_response(db, t)


@router.get("/{transfer_id}", response_model=AssetTransferResponse)
def get_transfer(transfer_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    return to_transfer_response(db, _load(db, transfer_id))


@router.post("/{transfer_id}/approve", response_model=AssetTransferResponse)
def approve_transfer(
    transfer_id: UUID,
    body: AssetTransferDecisionBody = AssetTransferDecisionBody(),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = _load(db, transfer_id)
    assert_transition("transfer", t.status, AssetTransferStatus.APPROVED)
    t.status = AssetTransferStatus.APPROVED
    t.approved_by_user_id = admin.id
    if body.notes:
        t.notes = (t.notes or "") + ("\n" if t.notes else "") + body.notes
    db.flush()
    write_asset_history(
        db, t.asset_id, AssetEventType.TRANSFER_APPROVED, actor_user_id=admin.id,
        related_entity_type="transfer", related_entity_id=t.id,
    )
    db.commit()
    db.refresh(t)
    return to_transfer_response(db, t)


@router.post("/{transfer_id}/reject", response_model=AssetTransferResponse)
def reject_transfer(
    transfer_id: UUID,
    body: AssetTransferDecisionBody = AssetTransferDecisionBody(),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = _load(db, transfer_id)
    assert_transition("transfer", t.status, AssetTransferStatus.REJECTED)
    t.status = AssetTransferStatus.REJECTED
    if body.notes:
        t.notes = (t.notes or "") + ("\n" if t.notes else "") + body.notes
    db.flush()
    write_asset_history(
        db, t.asset_id, AssetEventType.TRANSFER_REJECTED, actor_user_id=admin.id,
        related_entity_type="transfer", related_entity_id=t.id,
    )
    db.commit()
    db.refresh(t)
    return to_transfer_response(db, t)


@router.post("/{transfer_id}/cancel", response_model=AssetTransferResponse)
def cancel_transfer(
    transfer_id: UUID,
    body: AssetTransferDecisionBody = AssetTransferDecisionBody(),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = _load(db, transfer_id)
    assert_transition("transfer", t.status, AssetTransferStatus.CANCELLED)
    t.status = AssetTransferStatus.CANCELLED
    if body.notes:
        t.notes = (t.notes or "") + ("\n" if t.notes else "") + body.notes
    db.commit()
    db.refresh(t)
    return to_transfer_response(db, t)


@router.post("/{transfer_id}/complete", response_model=AssetTransferResponse)
def complete_transfer(
    transfer_id: UUID,
    body: AssetTransferDecisionBody = AssetTransferDecisionBody(),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    t = _load(db, transfer_id)
    assert_transition("transfer", t.status, AssetTransferStatus.COMPLETED)
    asset = db.query(Asset).filter(Asset.id == t.asset_id, Asset.is_deleted == False).first()  # noqa: E712
    if not asset:
        raise HTTPException(404, "Asset not found")
    if asset.status in (AssetStatus.MAINTENANCE, AssetStatus.RETIRED):
        raise HTTPException(409, f"Asset is {asset.status.value}; cannot complete transfer.")

    prior = asset.status
    today = body.disposed_date or t.effective_date or date.today()

    # Close the outgoing allocation (if the asset was held by someone).
    if t.transfer_type in _FROM_EMPLOYEE:
        open_alloc = None
        if t.old_allocation_id:
            open_alloc = db.query(AssetAllocation).filter(AssetAllocation.id == t.old_allocation_id).first()
        if not open_alloc:
            open_alloc = db.query(AssetAllocation).filter(
                AssetAllocation.asset_id == asset.id,
                AssetAllocation.status == AllocationStatus.ALLOCATED,
            ).order_by(AssetAllocation.created_at.desc()).first()
        if open_alloc and open_alloc.status == AllocationStatus.ALLOCATED:
            open_alloc.status = AllocationStatus.RETURNED
            open_alloc.returned_date = today
            open_alloc.returned_to_user_id = admin.id
            t.old_allocation_id = open_alloc.id

    # Open the incoming allocation (employee destination) or settle store/location.
    if t.transfer_type in _TO_EMPLOYEE and t.to_employee_id:
        new_alloc = AssetAllocation(
            asset_id=asset.id, employee_id=t.to_employee_id,
            allocated_date=today, condition_on_issue=asset.condition,
            status=AllocationStatus.ALLOCATED, issued_by_user_id=admin.id,
            notes=f"Via transfer {t.id}",
        )
        db.add(new_alloc)
        db.flush()
        t.new_allocation_id = new_alloc.id
        asset.status = AssetStatus.ALLOCATED
        asset.assigned_employee_id = t.to_employee_id
    elif t.transfer_type == AssetTransferType.EMPLOYEE_TO_STORE:
        asset.status = AssetStatus.AVAILABLE
        asset.assigned_employee_id = None

    # Apply location / department moves.
    if t.to_location_id:
        asset.location_id = t.to_location_id
    if t.to_department_id:
        asset.department_id = t.to_department_id

    t.status = AssetTransferStatus.COMPLETED
    if body.notes:
        t.notes = (t.notes or "") + ("\n" if t.notes else "") + body.notes
    db.flush()
    write_asset_history(
        db, asset.id, AssetEventType.TRANSFER_COMPLETED, actor_user_id=admin.id,
        from_status=prior, to_status=asset.status,
        related_entity_type="transfer", related_entity_id=t.id,
    )
    db.commit()
    db.refresh(t)
    return to_transfer_response(db, t)
