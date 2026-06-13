"""HR Shift Swap Requests — request → peer accept → manager approve → exchange.

On approval the two employees' one-day shift assignments for ``swap_date`` are
exchanged (idempotent; closes prior different-shift overlaps for that day).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.shift import Shift, EmployeeShiftAssignment
from app.models.hr.shift_swap import ShiftSwapRequest, SwapStatus
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.shift_ops import ShiftSwapCreate, SwapDecisionBody, ShiftSwapResponse
from app.utils.dependencies import get_current_superuser
from app.utils.hr.attendance_logic import log

router = APIRouter(prefix="/hr/shift-swaps", tags=["HR — Shift Swaps"])


def _emp_name(db: Session, emp_id) -> Optional[str]:
    if not emp_id:
        return None
    row = (db.query(User.full_name).join(Employee, Employee.user_id == User.id)
           .filter(Employee.id == emp_id).first())
    return row[0] if row else None


def _shift_code(db: Session, shift_id) -> Optional[str]:
    if not shift_id:
        return None
    row = db.query(Shift.code).filter(Shift.id == shift_id).first()
    return row[0] if row else None


def _resp(db: Session, s: ShiftSwapRequest) -> ShiftSwapResponse:
    return ShiftSwapResponse(
        id=s.id, requester_employee_id=s.requester_employee_id,
        requester_name=_emp_name(db, s.requester_employee_id),
        counterparty_employee_id=s.counterparty_employee_id,
        counterparty_name=_emp_name(db, s.counterparty_employee_id),
        swap_date=s.swap_date,
        requester_shift_id=s.requester_shift_id, requester_shift_code=_shift_code(db, s.requester_shift_id),
        counterparty_shift_id=s.counterparty_shift_id, counterparty_shift_code=_shift_code(db, s.counterparty_shift_id),
        reason=s.reason,
        status=s.status.value if hasattr(s.status, "value") else str(s.status),
        peer_accepted=s.peer_accepted, decision_notes=s.decision_notes,
        decided_at=s.decided_at, created_at=s.created_at)


def _reassign_one_day(db, emp_id, shift_id, day, admin_id, note):
    """Create a one-day assignment, closing prior different-shift overlaps. Idempotent."""
    if not shift_id:
        return
    exists = db.query(EmployeeShiftAssignment).filter(
        EmployeeShiftAssignment.employee_id == emp_id,
        EmployeeShiftAssignment.shift_id == shift_id,
        EmployeeShiftAssignment.effective_from == day,
        EmployeeShiftAssignment.effective_until == day).first()
    if exists:
        return
    prior = (db.query(EmployeeShiftAssignment)
             .filter(EmployeeShiftAssignment.employee_id == emp_id,
                     EmployeeShiftAssignment.effective_from <= day,
                     or_(EmployeeShiftAssignment.effective_until.is_(None),
                         EmployeeShiftAssignment.effective_until >= day)).all())
    for p in prior:
        if p.shift_id != shift_id:
            p.effective_until = day - timedelta(days=1)
    db.add(EmployeeShiftAssignment(
        employee_id=emp_id, shift_id=shift_id, effective_from=day, effective_until=day,
        notes=note, created_by_id=admin_id))


@router.get("/", response_model=dict)
def list_swaps(
    status: Optional[str] = None,
    employee_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(ShiftSwapRequest).filter(ShiftSwapRequest.is_deleted == False)  # noqa: E712
    if status:
        q = q.filter(ShiftSwapRequest.status == status)
    if employee_id:
        q = q.filter(or_(ShiftSwapRequest.requester_employee_id == employee_id,
                         ShiftSwapRequest.counterparty_employee_id == employee_id))
    total = q.count()
    rows = q.order_by(ShiftSwapRequest.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"items": [_resp(db, s) for s in rows], "total": total, "page": page, "limit": limit,
            "total_pages": ceil(total / limit) if limit else 1}


@router.post("/", response_model=ShiftSwapResponse, status_code=http_status.HTTP_201_CREATED)
def create_swap(
    payload: ShiftSwapCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if payload.requester_employee_id == payload.counterparty_employee_id:
        raise HTTPException(400, "Requester and counterparty must differ")
    for eid in (payload.requester_employee_id, payload.counterparty_employee_id):
        if not db.query(Employee).filter(Employee.id == eid, Employee.is_deleted == False).first():  # noqa: E712
            raise HTTPException(404, "Employee not found")
    s = ShiftSwapRequest(
        requester_employee_id=payload.requester_employee_id,
        counterparty_employee_id=payload.counterparty_employee_id,
        swap_date=payload.swap_date, requester_shift_id=payload.requester_shift_id,
        counterparty_shift_id=payload.counterparty_shift_id, reason=payload.reason,
        status=SwapStatus.PENDING_PEER, created_by_id=admin.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _resp(db, s)


@router.post("/{swap_id}/accept", response_model=ShiftSwapResponse)
def accept_swap(swap_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    s = db.query(ShiftSwapRequest).filter(ShiftSwapRequest.id == swap_id, ShiftSwapRequest.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Swap not found")
    if s.status != SwapStatus.PENDING_PEER:
        raise HTTPException(409, f"Cannot accept a swap in {s.status.value} state")
    s.peer_accepted = True
    s.status = SwapStatus.PENDING_MANAGER
    db.commit()
    db.refresh(s)
    return _resp(db, s)


@router.patch("/{swap_id}/approve", response_model=ShiftSwapResponse)
def approve_swap(swap_id: UUID, body: SwapDecisionBody = SwapDecisionBody(), db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    s = db.query(ShiftSwapRequest).filter(ShiftSwapRequest.id == swap_id, ShiftSwapRequest.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Swap not found")
    if s.status not in (SwapStatus.PENDING_MANAGER, SwapStatus.PENDING_PEER):
        raise HTTPException(409, f"Cannot approve a swap in {s.status.value} state")
    # exchange the two one-day assignments
    note = f"Swap · {s.swap_date.isoformat()}"
    _reassign_one_day(db, s.requester_employee_id, s.counterparty_shift_id, s.swap_date, admin.id, note)
    _reassign_one_day(db, s.counterparty_employee_id, s.requester_shift_id, s.swap_date, admin.id, note)
    s.status = SwapStatus.APPROVED
    s.peer_accepted = True
    s.decision_notes = body.notes
    s.decided_by_id = admin.id
    s.decided_at = datetime.utcnow()
    db.flush()
    log(db, actor_id=admin.id, action=AttendanceLogAction.SHIFT_ASSIGNED,
        target_table="hr_shift_swap_requests", target_id=s.id,
        payload={"swap_date": s.swap_date.isoformat(), "status": "APPROVED"})
    db.commit()
    db.refresh(s)
    return _resp(db, s)


@router.patch("/{swap_id}/reject", response_model=ShiftSwapResponse)
def reject_swap(swap_id: UUID, body: SwapDecisionBody = SwapDecisionBody(), db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    s = db.query(ShiftSwapRequest).filter(ShiftSwapRequest.id == swap_id, ShiftSwapRequest.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Swap not found")
    if s.status in (SwapStatus.APPROVED, SwapStatus.REJECTED, SwapStatus.CANCELLED):
        raise HTTPException(409, f"Cannot reject a swap in {s.status.value} state")
    s.status = SwapStatus.REJECTED
    s.decision_notes = body.notes
    s.decided_by_id = admin.id
    s.decided_at = datetime.utcnow()
    db.commit()
    db.refresh(s)
    return _resp(db, s)


@router.post("/{swap_id}/cancel", response_model=ShiftSwapResponse)
def cancel_swap(swap_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    s = db.query(ShiftSwapRequest).filter(ShiftSwapRequest.id == swap_id, ShiftSwapRequest.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Swap not found")
    if s.status == SwapStatus.APPROVED:
        raise HTTPException(409, "Cannot cancel an approved swap")
    s.status = SwapStatus.CANCELLED
    db.commit()
    db.refresh(s)
    return _resp(db, s)


@router.delete("/{swap_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_swap(swap_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    s = db.query(ShiftSwapRequest).filter(ShiftSwapRequest.id == swap_id).first()
    if not s:
        raise HTTPException(404, "Swap not found")
    s.is_deleted = True
    db.commit()
