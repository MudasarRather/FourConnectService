"""HR Holiday Shifts — staff working on holidays + their compensation rule."""
from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.shift import Shift
from app.models.hr.holiday import Holiday
from app.models.hr.holiday_shift import HolidayShiftAssignment, HolidayCompType
from app.schemas.hr.shift_ops import (
    HolidayShiftCreate, HolidayShiftBulkBody, HolidayShiftUpdate, HolidayShiftResponse,
    HolidayShiftRemoveBody,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/holiday-shifts", tags=["HR — Holiday Shifts"])

_DEFAULT_MULT = {"DOUBLE_PAY": 2.0, "OVERTIME": 1.5, "HOLIDAY_ALLOWANCE": 1.0, "COMP_OFF": 1.0}


def _emp_name(db, eid):
    row = (db.query(User.full_name).join(Employee, Employee.user_id == User.id)
           .filter(Employee.id == eid).first())
    return row[0] if row else None


def _resp(db: Session, a: HolidayShiftAssignment) -> HolidayShiftResponse:
    hol = db.query(Holiday).filter(Holiday.id == a.holiday_id).first()
    sh = db.query(Shift).filter(Shift.id == a.shift_id).first() if a.shift_id else None
    return HolidayShiftResponse(
        id=a.id, holiday_id=a.holiday_id,
        holiday_name=hol.name if hol else None, holiday_date=hol.date if hol else None,
        employee_id=a.employee_id, employee_name=_emp_name(db, a.employee_id),
        shift_id=a.shift_id, shift_code=sh.code if sh else None, shift_name=sh.name if sh else None,
        compensation=a.compensation.value if hasattr(a.compensation, "value") else str(a.compensation),
        pay_multiplier=float(a.pay_multiplier or 0), notes=a.notes, created_at=a.created_at)


def _comp(v: str) -> HolidayCompType:
    if v not in HolidayCompType.__members__:
        raise HTTPException(400, "Invalid compensation type")
    return HolidayCompType(v)


@router.get("/", response_model=dict)
def list_assignments(
    holiday_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(HolidayShiftAssignment).filter(HolidayShiftAssignment.is_deleted == False)  # noqa: E712
    if holiday_id:
        q = q.filter(HolidayShiftAssignment.holiday_id == holiday_id)
    total = q.count()
    rows = q.order_by(HolidayShiftAssignment.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"items": [_resp(db, a) for a in rows], "total": total, "page": page, "limit": limit,
            "total_pages": ceil(total / limit) if limit else 1}


@router.post("/", response_model=HolidayShiftResponse, status_code=http_status.HTTP_201_CREATED)
def create_assignment(
    payload: HolidayShiftCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    hol = db.query(Holiday).filter(Holiday.id == payload.holiday_id, Holiday.is_deleted == False).first()  # noqa: E712
    if not hol:
        raise HTTPException(404, "Holiday not found")
    if not hol.is_active:
        raise HTTPException(400, "This holiday is still a draft. Apply it in Attendance > Holidays before staffing it.")
    if not db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Employee not found")
    # uq_holiday_emp is unique on (holiday_id, employee_id) and ignores is_deleted, so a
    # previously-removed (tombstoned) row makes a fresh INSERT raise UniqueViolation.
    # Look up ANY existing row and resurrect a tombstoned one instead of re-inserting.
    existing = db.query(HolidayShiftAssignment).filter(
        HolidayShiftAssignment.holiday_id == payload.holiday_id,
        HolidayShiftAssignment.employee_id == payload.employee_id).first()
    if existing and not existing.is_deleted:
        raise HTTPException(409, "Employee already assigned to this holiday")
    if existing:  # tombstoned → resurrect with the new compensation, clear stand-down audit
        existing.is_deleted = False
        existing.shift_id = payload.shift_id
        existing.compensation = _comp(payload.compensation)
        existing.pay_multiplier = payload.pay_multiplier
        existing.notes = payload.notes
        existing.created_by_id = admin.id
        existing.removal_reason = existing.removal_category = None
        existing.removed_at = existing.removed_by_id = None
        db.commit()
        db.refresh(existing)
        return _resp(db, existing)
    a = HolidayShiftAssignment(
        holiday_id=payload.holiday_id, employee_id=payload.employee_id, shift_id=payload.shift_id,
        compensation=_comp(payload.compensation), pay_multiplier=payload.pay_multiplier,
        notes=payload.notes, created_by_id=admin.id)
    db.add(a)
    db.commit()
    db.refresh(a)
    return _resp(db, a)


@router.post("/bulk", response_model=dict, status_code=http_status.HTTP_201_CREATED)
def bulk_assign(
    body: HolidayShiftBulkBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    hol = db.query(Holiday).filter(Holiday.id == body.holiday_id, Holiday.is_deleted == False).first()  # noqa: E712
    if not hol:
        raise HTTPException(404, "Holiday not found")
    if not hol.is_active:
        raise HTTPException(400, "This holiday is still a draft. Apply it in Attendance > Holidays before staffing it.")
    comp = _comp(body.compensation)
    created, skipped = 0, 0
    for eid in body.employee_ids:
        if not db.query(Employee).filter(Employee.id == eid, Employee.is_deleted == False).first():  # noqa: E712
            continue
        # Resurrect a tombstoned row rather than re-INSERT (uq_holiday_emp ignores is_deleted).
        existing = db.query(HolidayShiftAssignment).filter(
            HolidayShiftAssignment.holiday_id == body.holiday_id,
            HolidayShiftAssignment.employee_id == eid).first()
        if existing and not existing.is_deleted:
            skipped += 1
            continue
        if existing:
            existing.is_deleted = False
            existing.shift_id = body.shift_id
            existing.compensation = comp
            existing.pay_multiplier = body.pay_multiplier
            existing.created_by_id = admin.id
            existing.removal_reason = existing.removal_category = None
            existing.removed_at = existing.removed_by_id = None
            created += 1
            continue
        db.add(HolidayShiftAssignment(
            holiday_id=body.holiday_id, employee_id=eid, shift_id=body.shift_id,
            compensation=comp, pay_multiplier=body.pay_multiplier, created_by_id=admin.id))
        created += 1
    db.commit()
    return {"assigned": created, "skipped": skipped}


@router.patch("/{assignment_id}", response_model=HolidayShiftResponse)
def update_assignment(
    assignment_id: UUID,
    payload: HolidayShiftUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(HolidayShiftAssignment).filter(HolidayShiftAssignment.id == assignment_id, HolidayShiftAssignment.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Assignment not found")
    data = payload.model_dump(exclude_unset=True)
    if "compensation" in data and data["compensation"]:
        a.compensation = _comp(data.pop("compensation"))
    for k, v in data.items():
        setattr(a, k, v)
    db.commit()
    db.refresh(a)
    return _resp(db, a)


@router.delete("/{assignment_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: UUID,
    body: Optional[HolidayShiftRemoveBody] = Body(default=None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Stand an employee down from a holiday shift (soft delete).

    The Holiday Roster remove modal sends an optional reason + category in the
    DELETE body; we persist it on the row for audit. Callers without a body
    (legacy) still work — the removal is recorded with who/when only.
    """
    a = db.query(HolidayShiftAssignment).filter(HolidayShiftAssignment.id == assignment_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")
    if a.is_deleted:
        return  # idempotent — already stood down
    a.is_deleted = True
    a.removed_at = datetime.now(timezone.utc)
    a.removed_by_id = admin.id
    if body:
        a.removal_reason = (body.reason or None)
        a.removal_category = (body.reason_category or None)
    db.commit()
