"""HR Shifts — templates + effective-dated employee assignments."""
from __future__ import annotations

from datetime import date, timedelta
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.shift import Shift, EmployeeShiftAssignment, ShiftType
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.attendance import (
    ShiftCreate, ShiftUpdate, ShiftResponse, ShiftListResponse,
    EmployeeShiftAssignmentCreate, EmployeeShiftAssignmentResponse, ShiftAssignBulkBody,
)
from app.utils.dependencies import get_current_user, get_current_superuser
from app.utils.hr.attendance_logic import log

router = APIRouter(prefix="/hr/shifts", tags=["HR — Shifts"])


def _to_shift_response(s: Shift) -> ShiftResponse:
    return ShiftResponse(
        id=s.id, code=s.code, name=s.name, shift_type=s.shift_type,
        start_time=s.start_time, end_time=s.end_time,
        break_minutes=s.break_minutes, grace_minutes=s.grace_minutes,
        weekly_off_days=s.weekly_off_days or [],
        half_day_hours=float(s.half_day_hours or 0),
        full_day_hours=float(s.full_day_hours or 0),
        night_allowance=bool(s.night_allowance),
        description=s.description, is_active=bool(s.is_active),
        created_at=s.created_at,
    )


def _to_assignment_response(db: Session, a: EmployeeShiftAssignment) -> EmployeeShiftAssignmentResponse:
    shift = db.query(Shift).filter(Shift.id == a.shift_id).first()
    name_row = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == a.employee_id)
        .first()
    )
    return EmployeeShiftAssignmentResponse(
        id=a.id, employee_id=a.employee_id,
        employee_name=name_row[0] if name_row else None,
        shift_id=a.shift_id,
        shift_code=shift.code if shift else None,
        shift_name=shift.name if shift else None,
        effective_from=a.effective_from,
        effective_until=a.effective_until,
        is_default=bool(a.is_default),
        notes=a.notes,
        created_at=a.created_at,
    )


# ── Shifts CRUD ────────────────────────────────────────────────────────────

@router.get("/", response_model=ShiftListResponse)
def list_shifts(
    is_active: Optional[bool] = None,
    shift_type: Optional[ShiftType] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(Shift).filter(Shift.is_deleted == False)  # noqa: E712
    if is_active is not None:
        q = q.filter(Shift.is_active == is_active)
    if shift_type:
        q = q.filter(Shift.shift_type == shift_type)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(or_(
            func.lower(Shift.code).like(like),
            func.lower(Shift.name).like(like),
        ))
    total = q.count()
    rows = q.order_by(Shift.created_at.asc()).offset((page - 1) * limit).limit(limit).all()
    return ShiftListResponse(
        items=[_to_shift_response(r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.get("/me/current", response_model=Optional[ShiftResponse])
def my_current_shift(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # Returning null here instead of raising 404 keeps the network tab clean
    # for admins (who typically don't have a linked Employee row). The
    # frontend reads `data == null` and just hides the radial shift timer.
    emp = db.query(Employee).filter(Employee.user_id == user.id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        return None
    from app.utils.hr.attendance_logic import resolve_shift
    s = resolve_shift(db, emp.id, date.today())
    return _to_shift_response(s) if s else None


# ── Assignments ───────────────────────────────────────────────────────────
# IMPORTANT: every literal-path route below ("/assignments", "/assignments/{id}")
# MUST be declared BEFORE the catch-all "/{shift_id}" routes. FastAPI matches
# in declaration order, so if "/{shift_id}" came first the frontend's
# `GET /api/hr/shifts/assignments?active_on=YYYY-MM-DD` would try to parse
# "assignments" as a UUID and return 422 instead of reaching this handler.

@router.get("/assignments", response_model=List[EmployeeShiftAssignmentResponse])
def list_assignments(
    employee_id: Optional[UUID] = None,
    shift_id: Optional[UUID] = None,
    active_on: Optional[date] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(EmployeeShiftAssignment)
    if employee_id:
        q = q.filter(EmployeeShiftAssignment.employee_id == employee_id)
    if shift_id:
        q = q.filter(EmployeeShiftAssignment.shift_id == shift_id)
    if active_on:
        q = q.filter(EmployeeShiftAssignment.effective_from <= active_on)
        q = q.filter(or_(EmployeeShiftAssignment.effective_until.is_(None),
                        EmployeeShiftAssignment.effective_until >= active_on))
    rows = q.order_by(EmployeeShiftAssignment.effective_from.desc()).limit(500).all()
    return [_to_assignment_response(db, a) for a in rows]


@router.post("/assignments", response_model=EmployeeShiftAssignmentResponse, status_code=http_status.HTTP_201_CREATED)
def create_assignment(
    payload: EmployeeShiftAssignmentCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if not db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Employee not found")
    if not db.query(Shift).filter(Shift.id == payload.shift_id, Shift.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Shift not found")
    # Close any prior active assignment for this employee
    prior = (
        db.query(EmployeeShiftAssignment)
        .filter(EmployeeShiftAssignment.employee_id == payload.employee_id)
        .filter(or_(EmployeeShiftAssignment.effective_until.is_(None),
                    EmployeeShiftAssignment.effective_until >= payload.effective_from))
        .all()
    )
    for p in prior:
        p.effective_until = payload.effective_from - timedelta(days=1)
    a = EmployeeShiftAssignment(
        employee_id=payload.employee_id,
        shift_id=payload.shift_id,
        effective_from=payload.effective_from,
        effective_until=payload.effective_until,
        is_default=payload.is_default,
        notes=payload.notes,
        created_by_id=admin.id,
    )
    db.add(a)
    if payload.is_default:
        emp = db.query(Employee).filter(Employee.id == payload.employee_id).first()
        if emp:
            emp.shift_id = payload.shift_id
    db.flush()
    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.SHIFT_ASSIGNED,
        target_table="hr_employee_shift_assignments",
        target_id=a.id,
        employee_id=payload.employee_id,
        payload={"shift_id": str(payload.shift_id), "effective_from": payload.effective_from.isoformat()},
    )
    db.commit()
    db.refresh(a)
    return _to_assignment_response(db, a)


@router.delete("/assignments/{assignment_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(EmployeeShiftAssignment).filter(EmployeeShiftAssignment.id == assignment_id).first()
    if not a:
        raise HTTPException(404, "Assignment not found")
    db.delete(a)
    db.commit()


# ── Shift CRUD (parametrized routes come AFTER the literal /assignments ones) ──

@router.get("/{shift_id}", response_model=ShiftResponse)
def get_shift(
    shift_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    s = db.query(Shift).filter(Shift.id == shift_id, Shift.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Shift not found")
    return _to_shift_response(s)


@router.post("/", response_model=ShiftResponse, status_code=http_status.HTTP_201_CREATED)
def create_shift(
    payload: ShiftCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if db.query(Shift).filter(Shift.code == payload.code).first():
        raise HTTPException(400, "Shift code already exists")
    s = Shift(**payload.model_dump(), created_by_id=admin.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_shift_response(s)


@router.patch("/{shift_id}", response_model=ShiftResponse)
def update_shift(
    shift_id: UUID,
    payload: ShiftUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    s = db.query(Shift).filter(Shift.id == shift_id, Shift.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Shift not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _to_shift_response(s)


@router.delete("/{shift_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_shift(
    shift_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    s = db.query(Shift).filter(Shift.id == shift_id).first()
    if not s:
        raise HTTPException(404, "Shift not found")
    active = (
        db.query(EmployeeShiftAssignment)
        .filter(EmployeeShiftAssignment.shift_id == shift_id)
        .filter(or_(EmployeeShiftAssignment.effective_until.is_(None),
                    EmployeeShiftAssignment.effective_until >= date.today()))
        .count()
    )
    if active:
        raise HTTPException(409, f"Cannot delete; {active} active assignment(s). Reassign first.")
    s.is_deleted = True
    db.commit()


@router.post("/{shift_id}/assign", status_code=http_status.HTTP_201_CREATED)
def bulk_assign_shift(
    shift_id: UUID,
    body: ShiftAssignBulkBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Assign one or more employees to a shift over a date range.

    Workflow rules:
      1. The new range [effective_from, effective_until] CANNOT overlap any
         existing assignment for the same employee on a DIFFERENT shift.
         The admin must explicitly unassign the conflicting row first.
      2. If an existing assignment for the SAME shift already covers the
         range, the call is idempotent — we don't create a duplicate row,
         we just extend the existing one if the new range goes further.
      3. We DO NOT silently close prior assignments anymore. That behaviour
         was confusing ("removed from another shift") and broke the audit
         trail.
    """
    target_shift = db.query(Shift).filter(Shift.id == shift_id, Shift.is_deleted == False).first()  # noqa: E712
    if not target_shift:
        raise HTTPException(404, "Shift not found")

    new_from = body.effective_from
    new_until = body.effective_until  # may be None for indefinite

    # Pre-flight: collect every conflict so we can return a single helpful 409.
    conflicts = []
    for emp_id in body.employee_ids:
        emp = db.query(Employee).filter(Employee.id == emp_id, Employee.is_deleted == False).first()  # noqa: E712
        if not emp:
            continue
        # Two ranges [a, b] and [c, d] overlap iff a <= d AND c <= b
        # (where None on `until` means +infinity).
        q = (
            db.query(EmployeeShiftAssignment, Shift)
            .join(Shift, Shift.id == EmployeeShiftAssignment.shift_id)
            .filter(EmployeeShiftAssignment.employee_id == emp_id)
            .filter(EmployeeShiftAssignment.shift_id != shift_id)
        )
        # new_from <= existing.effective_until (or existing is open-ended)
        q = q.filter(or_(
            EmployeeShiftAssignment.effective_until.is_(None),
            EmployeeShiftAssignment.effective_until >= new_from,
        ))
        # existing.effective_from <= new_until (or new is open-ended)
        if new_until is not None:
            q = q.filter(EmployeeShiftAssignment.effective_from <= new_until)
        existing_overlaps = q.all()
        for ea, es in existing_overlaps:
            name_row = (
                db.query(User.full_name)
                .join(Employee, Employee.user_id == User.id)
                .filter(Employee.id == emp_id)
                .first()
            )
            conflicts.append({
                "employee_id": str(emp_id),
                "employee_name": (name_row[0] if name_row else None) or emp.employee_id,
                "conflicting_shift_code": es.code,
                "conflicting_shift_name": es.name,
                "conflicting_from": ea.effective_from.isoformat(),
                "conflicting_until": ea.effective_until.isoformat() if ea.effective_until else None,
                "assignment_id": str(ea.id),
            })
    if conflicts:
        # 409 with a structured body the frontend can render nicely.
        names = ", ".join(sorted({c["employee_name"] for c in conflicts}))
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Cannot assign {names}: already on another shift in this date range. "
                    "Unassign the conflicting row first or pick a non-overlapping range."
                ),
                "conflicts": conflicts,
            },
        )

    created = 0
    skipped_same_shift = 0
    for emp_id in body.employee_ids:
        if not db.query(Employee).filter(Employee.id == emp_id, Employee.is_deleted == False).first():  # noqa: E712
            continue
        # Same-shift overlap → extend instead of duplicate.
        same_shift_existing = (
            db.query(EmployeeShiftAssignment)
            .filter(EmployeeShiftAssignment.employee_id == emp_id)
            .filter(EmployeeShiftAssignment.shift_id == shift_id)
            .filter(or_(
                EmployeeShiftAssignment.effective_until.is_(None),
                EmployeeShiftAssignment.effective_until >= new_from,
            ))
            .first()
        )
        if same_shift_existing:
            # Extend the upper bound if the new range goes further (or open-ends it).
            if new_until is None:
                same_shift_existing.effective_until = None
            elif same_shift_existing.effective_until is not None and new_until > same_shift_existing.effective_until:
                same_shift_existing.effective_until = new_until
            # Lower bound only moves earlier if the admin explicitly asked for it.
            if new_from < same_shift_existing.effective_from:
                same_shift_existing.effective_from = new_from
            skipped_same_shift += 1
            continue
        a = EmployeeShiftAssignment(
            employee_id=emp_id, shift_id=shift_id,
            effective_from=new_from, effective_until=new_until,
            is_default=body.is_default, notes=body.notes,
            created_by_id=admin.id,
        )
        db.add(a)
        if body.is_default:
            emp = db.query(Employee).filter(Employee.id == emp_id).first()
            if emp:
                emp.shift_id = shift_id
        created += 1
    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.SHIFT_ASSIGNED,
        target_table="hr_employee_shift_assignments",
        payload={"shift_id": str(shift_id), "count": created, "effective_from": new_from.isoformat()},
    )
    db.commit()
    return {"assigned": created, "extended": skipped_same_shift}
