"""HR Overtime — corporate OT workflow.

Two creation paths into the same OvertimeRequest pool, both landing in
status=PENDING for manager review:

  1. Pre-approval — employee submits POST /hr/overtime/me with date + hours +
     reason BEFORE working. Manager approves; employee then works that day
     knowing OT is sanctioned.
  2. Auto-detected — daily_rollup notices the employee clocked out past
     shift_end + grace (excluding break time, see attendance_logic._build_work_intervals)
     and creates the OT request retroactively with reason="Auto-detected …".

Either way: APPROVED OT feeds payroll. REJECTED OT stays on file for audit.
The self-service /me cluster lets employees see and cancel their own pending
requests; the admin cluster handles approvals.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.overtime import OvertimeRequest, OtStatus, OtPayrollStatus, OtType
from app.schemas.hr.attendance import (
    OvertimeCreate, OvertimeDecideBody, OvertimeResponse, OvertimeListResponse,
    MyOvertimeCreate,
)
from app.utils.dependencies import get_current_superuser, get_current_user

router = APIRouter(prefix="/hr/overtime", tags=["HR — Overtime"])


def _resolve_self(db: Session, user: User) -> Employee:
    emp = db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "No employee record linked to your account")
    return emp


def _to_response(db: Session, o: OvertimeRequest) -> OvertimeResponse:
    name = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == o.employee_id)
        .first()
    )
    return OvertimeResponse(
        id=o.id, employee_id=o.employee_id, employee_name=name[0] if name else None,
        date=o.date, ot_hours=float(o.ot_hours or 0), ot_type=o.ot_type,
        reason=o.reason, status=o.status, payroll_status=o.payroll_status,
        created_at=o.created_at,
    )


@router.get("/", response_model=OvertimeListResponse)
def list_ot(
    status_filter: Optional[OtStatus] = Query(None, alias="status"),
    employee_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(OvertimeRequest).filter(OvertimeRequest.is_deleted == False)  # noqa: E712
    if status_filter:
        q = q.filter(OvertimeRequest.status == status_filter)
    if employee_id:
        q = q.filter(OvertimeRequest.employee_id == employee_id)
    total = q.count()
    rows = q.order_by(OvertimeRequest.date.desc()).offset((page - 1) * limit).limit(limit).all()
    return OvertimeListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=OvertimeResponse, status_code=http_status.HTTP_201_CREATED)
def create_ot(
    payload: OvertimeCreate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    o = OvertimeRequest(**payload.model_dump())
    db.add(o)
    db.commit()
    db.refresh(o)
    return _to_response(db, o)


@router.patch("/{ot_id}/approve", response_model=OvertimeResponse)
def approve_ot(
    ot_id: UUID,
    body: OvertimeDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(OvertimeRequest).filter(OvertimeRequest.id == ot_id, OvertimeRequest.is_deleted == False).first()  # noqa: E712
    if not o:
        raise HTTPException(404, "OT request not found")
    if o.payroll_status == OtPayrollStatus.PROCESSED:
        raise HTTPException(409, "OT already payroll-processed; cannot change")
    if o.status != OtStatus.PENDING:
        raise HTTPException(409, f"OT already {o.status.value}")
    o.status = OtStatus.APPROVED
    o.approved_by_id = admin.id
    o.approved_at = datetime.now(timezone.utc)
    o.decision_notes = body.notes
    db.commit()
    db.refresh(o)
    return _to_response(db, o)


@router.patch("/{ot_id}/reject", response_model=OvertimeResponse)
def reject_ot(
    ot_id: UUID,
    body: OvertimeDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    o = db.query(OvertimeRequest).filter(OvertimeRequest.id == ot_id, OvertimeRequest.is_deleted == False).first()  # noqa: E712
    if not o:
        raise HTTPException(404, "OT request not found")
    if o.status != OtStatus.PENDING:
        raise HTTPException(409, f"OT already {o.status.value}")
    o.status = OtStatus.REJECTED
    o.approved_by_id = admin.id
    o.approved_at = datetime.now(timezone.utc)
    o.decision_notes = body.notes
    db.commit()
    db.refresh(o)
    return _to_response(db, o)


# ══════════════════════════════════════════════════════════════════════════
# Self-service /me block — employee submits, lists, and cancels their own
# OT requests. Pre-approval flow: submit BEFORE the OT day so the manager can
# approve it in advance.
# ══════════════════════════════════════════════════════════════════════════

@router.get("/me", response_model=OvertimeListResponse)
def list_my_ot(
    status_filter: Optional[OtStatus] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Employee's own OT requests — pre-submitted and auto-detected."""
    emp = _resolve_self(db, user)
    q = db.query(OvertimeRequest).filter(
        OvertimeRequest.employee_id == emp.id,
        OvertimeRequest.is_deleted == False,  # noqa: E712
    )
    if status_filter:
        q = q.filter(OvertimeRequest.status == status_filter)
    total = q.count()
    rows = (q.order_by(OvertimeRequest.date.desc(), OvertimeRequest.created_at.desc())
            .offset((page - 1) * limit).limit(limit).all())
    return OvertimeListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/me", response_model=OvertimeResponse, status_code=http_status.HTTP_201_CREATED)
def create_my_ot(
    payload: MyOvertimeCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Employee submits a pre-approval OT request for themselves.

    Validation rules (corporate norms):
      • Cannot pre-submit for a date more than 14 days in the past — older
        days should be handled by manager-side correction, not self-claim.
      • Cannot submit for a date more than 30 days in the future.
      • Duplicate detection — refuse if there's already a PENDING or APPROVED
        request from this employee for the same date. (REJECTED/CANCELLED
        rows are ignored so the employee can re-request after a rejection.)
    """
    emp = _resolve_self(db, user)
    from datetime import timedelta
    today = date.today()
    if payload.date < today - timedelta(days=14):
        raise HTTPException(422, "OT requests are limited to the last 14 days. Contact your manager for older claims.")
    if payload.date > today + timedelta(days=30):
        raise HTTPException(422, "OT requests are limited to the next 30 days.")
    dup = (
        db.query(OvertimeRequest)
        .filter(
            OvertimeRequest.employee_id == emp.id,
            OvertimeRequest.date == payload.date,
            OvertimeRequest.is_deleted == False,  # noqa: E712
            OvertimeRequest.status.in_([OtStatus.PENDING, OtStatus.APPROVED]),
        )
        .first()
    )
    if dup:
        raise HTTPException(409, f"An OT request for {payload.date.isoformat()} already exists in status {dup.status.value}.")
    o = OvertimeRequest(
        employee_id=emp.id,
        date=payload.date,
        ot_hours=round(payload.ot_hours, 2),
        ot_type=payload.ot_type,
        reason=payload.reason,
        status=OtStatus.PENDING,
        payroll_status=OtPayrollStatus.PENDING,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return _to_response(db, o)


@router.delete("/me/{ot_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def cancel_my_ot(
    ot_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Employee cancels their own PENDING OT request. Approved/Rejected/Processed
    rows stay locked — those require manager action."""
    emp = _resolve_self(db, user)
    o = (
        db.query(OvertimeRequest)
        .filter(OvertimeRequest.id == ot_id, OvertimeRequest.is_deleted == False)  # noqa: E712
        .first()
    )
    if not o:
        raise HTTPException(404, "OT request not found")
    if o.employee_id != emp.id:
        raise HTTPException(403, "You can only cancel your own OT requests")
    if o.status != OtStatus.PENDING:
        raise HTTPException(409, f"Only PENDING requests can be cancelled (this is {o.status.value})")
    o.status = OtStatus.CANCELLED
    db.commit()
    return None
