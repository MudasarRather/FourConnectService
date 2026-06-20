"""HR Asset Management — employee self-service (`/hr/me/assets`).

The regular (non-admin) employee views the assets issued to them, acknowledges
receipt (digital sign-off), reports damage (with photos), and requests a return.
Reads use ``try_self_employee`` (→ unlinked banner, no 404 spam); writes use
``resolve_self_employee`` + an ownership check on every allocation.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.asset import Asset, AssetAllocation, AssetStatus, AllocationStatus
from app.models.hr.asset_lifecycle import (
    AssetDamage, AssetDamageStatus, AssetHistory, AssetEventType,
)
from app.schemas.hr.asset import AssetAllocationResponse, ReturnRequestBody
from app.schemas.hr.asset_lifecycle import (
    DamageSelfReport, AssetDamageResponse, MyAssetsResponse, MyAssetSummary,
    AssetHistoryResponse,
)
from app.utils.dependencies import get_current_user
from app.utils.hr.reimbursements.service import try_self_employee, resolve_self_employee
from app.utils.hr.assets.audit import write_asset_history
from app.utils.hr.assets.responses import to_alloc_response, to_damage_response, to_history_response

router = APIRouter(prefix="/hr/me/assets", tags=["HR — My Assets"])


def _own_allocation(db: Session, allocation_id: UUID, emp: Employee) -> AssetAllocation:
    al = db.query(AssetAllocation).options(joinedload(AssetAllocation.asset)).filter(
        AssetAllocation.id == allocation_id
    ).first()
    if not al or al.employee_id != emp.id:
        raise HTTPException(404, "Allocation not found")
    return al


@router.get("/", response_model=MyAssetsResponse)
def my_assets(
    include_returned: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, user)
    if not emp:
        return MyAssetsResponse(items=[], unlinked=True)
    q = db.query(AssetAllocation).options(joinedload(AssetAllocation.asset)).filter(
        AssetAllocation.employee_id == emp.id
    )
    if not include_returned:
        q = q.filter(AssetAllocation.status == AllocationStatus.ALLOCATED)
    rows = q.order_by(AssetAllocation.created_at.desc()).limit(200).all()
    items = [to_alloc_response(db, al, al.asset) for al in rows]

    active = [al for al in rows if al.status == AllocationStatus.ALLOCATED]
    today = date.today()
    summary = MyAssetSummary(
        held=len(active),
        pending_ack=sum(1 for al in active if not al.acknowledged_by_employee),
        needs_return=sum(
            1 for al in active
            if al.expected_return_date and al.expected_return_date < today
        ),
    )
    return MyAssetsResponse(items=items, unlinked=False, summary=summary)


@router.get("/history", response_model=List[AssetHistoryResponse])
def my_asset_history(
    limit: int = Query(60, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = try_self_employee(db, user)
    if not emp:
        return []
    rows = (
        db.query(AssetHistory)
        .filter(AssetHistory.actor_employee_id == emp.id)
        .order_by(AssetHistory.created_at.desc())
        .limit(limit).all()
    )
    return [to_history_response(db, h) for h in rows]


@router.get("/{allocation_id}", response_model=AssetAllocationResponse)
def my_asset_detail(
    allocation_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, user)
    al = _own_allocation(db, allocation_id, emp)
    return to_alloc_response(db, al, al.asset)


@router.post("/{allocation_id}/acknowledge", response_model=AssetAllocationResponse)
def acknowledge_my_asset(
    allocation_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, user)
    al = _own_allocation(db, allocation_id, emp)
    if not al.acknowledged_by_employee:  # idempotent
        al.acknowledged_by_employee = True
        al.acknowledged_at = datetime.utcnow()
        db.flush()
        write_asset_history(
            db, al.asset_id, AssetEventType.ACKNOWLEDGED,
            actor_user_id=user.id, actor_employee_id=emp.id,
            related_entity_type="allocation", related_entity_id=al.id,
            note="Acknowledged by employee.",
        )
        db.commit()
        db.refresh(al)
    return to_alloc_response(db, al, al.asset)


@router.post("/{allocation_id}/report-damage", response_model=AssetDamageResponse)
def report_my_damage(
    allocation_id: UUID,
    payload: DamageSelfReport,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = resolve_self_employee(db, user)
    al = _own_allocation(db, allocation_id, emp)
    if al.status != AllocationStatus.ALLOCATED:
        raise HTTPException(409, "You can only report damage on an asset you currently hold.")
    d = AssetDamage(
        asset_id=al.asset_id, allocation_id=al.id,
        severity=payload.severity, status=AssetDamageStatus.REPORTED,
        reported_by_employee_id=emp.id, reported_by_user_id=user.id,
        title=payload.title, description=payload.description,
        attachments=payload.attachments or [], reported_date=date.today(),
    )
    db.add(d)
    db.flush()
    write_asset_history(
        db, al.asset_id, AssetEventType.DAMAGE_REPORTED,
        actor_user_id=user.id, actor_employee_id=emp.id,
        related_entity_type="damage", related_entity_id=d.id,
        note=payload.title or "Self-reported damage.",
    )
    db.commit()
    db.refresh(d)
    return to_damage_response(db, d)


@router.post("/{allocation_id}/request-return", response_model=AssetAllocationResponse)
def request_my_return(
    allocation_id: UUID,
    payload: ReturnRequestBody = ReturnRequestBody(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Employee flags an asset for return. This is a *request* on the allocation —
    it surfaces in HR's Returns tab where HR completes the actual recovery. No
    transfer record is created (a return is not a transfer). Idempotent."""
    emp = resolve_self_employee(db, user)
    al = _own_allocation(db, allocation_id, emp)
    if al.status != AllocationStatus.ALLOCATED:
        raise HTTPException(409, "This asset is not currently allocated to you.")
    if getattr(al, "return_requested", False):
        # Already pending — refresh the note if a new one was supplied, stay idempotent.
        if payload.note and payload.note.strip():
            al.return_request_note = payload.note.strip()[:500]
            db.commit()
            db.refresh(al)
        resp = to_alloc_response(db, al, al.asset)
        resp.return_requested = True
        return resp
    al.return_requested = True
    al.return_requested_at = datetime.utcnow()
    al.return_request_note = (payload.note.strip()[:500] if payload.note and payload.note.strip() else None)
    db.flush()
    write_asset_history(
        db, al.asset_id, AssetEventType.RETURN_REQUESTED,
        actor_user_id=user.id, actor_employee_id=emp.id,
        related_entity_type="allocation", related_entity_id=al.id,
        note=al.return_request_note or "Employee requested return.",
    )
    db.commit()
    db.refresh(al)
    return to_alloc_response(db, al, al.asset)


@router.post("/{allocation_id}/cancel-return-request", response_model=AssetAllocationResponse)
def cancel_my_return_request(
    allocation_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Withdraw a pending return request the employee raised. Idempotent."""
    emp = resolve_self_employee(db, user)
    al = _own_allocation(db, allocation_id, emp)
    if getattr(al, "return_requested", False):
        al.return_requested = False
        al.return_requested_at = None
        al.return_request_note = None
        db.flush()
        write_asset_history(
            db, al.asset_id, AssetEventType.RETURN_REQUEST_CANCELLED,
            actor_user_id=user.id, actor_employee_id=emp.id,
            related_entity_type="allocation", related_entity_id=al.id,
            note="Employee withdrew return request.",
        )
        db.commit()
        db.refresh(al)
    return to_alloc_response(db, al, al.asset)
