"""HR Shift Rotations — cyclic shift schedules + auto-advance.

Advancing a rotation materialises the *next* step for each member into a
concrete `EmployeeShiftAssignment` window (idempotent), then bumps the cursor.
Reuses the same "close prior overlapping assignment" rule as
`app.routers.hr.shifts.create_assignment`.
"""
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
from app.models.hr.shift import Shift, EmployeeShiftAssignment
from app.models.hr.shift_rotation import (
    ShiftRotation, ShiftRotationStep, ShiftRotationMember, RotationCycle,
)
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.shift_planning import (
    ShiftRotationCreate, ShiftRotationUpdate, ShiftRotationResponse,
    RotationStepResponse, RotationMemberResponse, RotationAdvanceResult,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.attendance_logic import log

router = APIRouter(prefix="/hr/shift-rotations", tags=["HR — Shift Rotations"])

_CYCLE_DAYS = {"WEEKLY": 7, "BIWEEKLY": 14, "MONTHLY": 30}


def _freq_days(cycle: str, explicit: Optional[int]) -> int:
    if cycle == "CUSTOM":
        return max(1, int(explicit or 7))
    return _CYCLE_DAYS.get(cycle, 7)


def _emp_names(db: Session, emp_ids) -> dict:
    if not emp_ids:
        return {}
    rows = (
        db.query(Employee.id, User.full_name)
        .join(User, User.id == Employee.user_id)
        .filter(Employee.id.in_(list(emp_ids))).all())
    return {eid: name for eid, name in rows}


def _shift_meta(db: Session, shift_ids) -> dict:
    ids = [s for s in shift_ids if s]
    if not ids:
        return {}
    return {s.id: s for s in db.query(Shift).filter(Shift.id.in_(ids)).all()}


def _rotation_response(db: Session, r: ShiftRotation) -> ShiftRotationResponse:
    steps = sorted(r.steps, key=lambda s: s.sequence)
    members = list(r.members)
    smeta = _shift_meta(db, [s.shift_id for s in steps])
    names = _emp_names(db, [m.employee_id for m in members])
    step_out = [
        RotationStepResponse(
            id=s.id, sequence=s.sequence, shift_id=s.shift_id,
            shift_code=(smeta.get(s.shift_id).code if smeta.get(s.shift_id) else None),
            shift_name=(smeta.get(s.shift_id).name if smeta.get(s.shift_id) else None),
            label=s.label,
        ) for s in steps]
    member_out = [
        RotationMemberResponse(
            id=m.id, employee_id=m.employee_id,
            employee_name=names.get(m.employee_id), phase_offset=m.phase_offset)
        for m in members]
    cur_label = None
    if steps:
        cs = steps[r.current_step_index % len(steps)]
        cur_label = cs.label or (smeta.get(cs.shift_id).name if smeta.get(cs.shift_id) else "Off")
    return ShiftRotationResponse(
        id=r.id, name=r.name, code=r.code,
        cycle=r.cycle.value if hasattr(r.cycle, "value") else str(r.cycle),
        frequency_days=r.frequency_days, description=r.description,
        department_ids=r.department_ids or [], anchor_date=r.anchor_date,
        current_step_index=r.current_step_index, last_advanced_on=r.last_advanced_on,
        is_active=r.is_active, created_at=r.created_at,
        steps=step_out, members=member_out, member_count=len(member_out),
        current_step_label=cur_label,
    )


def _sync_steps(db: Session, r: ShiftRotation, steps_in):
    for s in list(r.steps):
        db.delete(s)
    db.flush()
    for i, st in enumerate(steps_in or []):
        db.add(ShiftRotationStep(
            rotation_id=r.id, sequence=st.sequence if st.sequence is not None else i,
            shift_id=st.shift_id, label=st.label))


def _sync_members(db: Session, r: ShiftRotation, emp_ids):
    for m in list(r.members):
        db.delete(m)
    db.flush()
    for i, eid in enumerate(emp_ids or []):
        db.add(ShiftRotationMember(rotation_id=r.id, employee_id=eid, phase_offset=i % max(1, len(emp_ids or [1]))))


@router.get("/", response_model=dict)
def list_rotations(
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(ShiftRotation).filter(ShiftRotation.is_deleted == False)  # noqa: E712
    if is_active is not None:
        q = q.filter(ShiftRotation.is_active == is_active)
    if search:
        like = f"%{search.lower()}%"
        q = q.filter(func.lower(ShiftRotation.name).like(like))
    total = q.count()
    rows = q.order_by(ShiftRotation.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [_rotation_response(db, r) for r in rows],
        "total": total, "page": page, "limit": limit,
        "total_pages": ceil(total / limit) if limit else 1,
    }


@router.post("/", response_model=ShiftRotationResponse, status_code=http_status.HTTP_201_CREATED)
def create_rotation(
    payload: ShiftRotationCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if payload.code and db.query(ShiftRotation).filter(ShiftRotation.code == payload.code).first():
        raise HTTPException(400, "Rotation code already exists")
    r = ShiftRotation(
        name=payload.name, code=payload.code or None,
        cycle=RotationCycle(payload.cycle) if payload.cycle in RotationCycle.__members__ else RotationCycle.WEEKLY,
        frequency_days=_freq_days(payload.cycle, payload.frequency_days),
        description=payload.description, department_ids=[str(d) for d in (payload.department_ids or [])],
        anchor_date=payload.anchor_date or date.today(), created_by_id=admin.id,
    )
    db.add(r)
    db.flush()
    _sync_steps(db, r, payload.steps)
    _sync_members(db, r, payload.member_employee_ids)
    db.commit()
    db.refresh(r)
    return _rotation_response(db, r)


@router.get("/{rotation_id}", response_model=ShiftRotationResponse)
def get_rotation(
    rotation_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRotation).filter(
        ShiftRotation.id == rotation_id, ShiftRotation.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rotation not found")
    return _rotation_response(db, r)


@router.patch("/{rotation_id}", response_model=ShiftRotationResponse)
def update_rotation(
    rotation_id: UUID,
    payload: ShiftRotationUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRotation).filter(
        ShiftRotation.id == rotation_id, ShiftRotation.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rotation not found")
    data = payload.model_dump(exclude_unset=True)
    if "name" in data: r.name = data["name"]
    if "code" in data: r.code = data["code"] or None
    if "description" in data: r.description = data["description"]
    if "is_active" in data: r.is_active = data["is_active"]
    if "anchor_date" in data: r.anchor_date = data["anchor_date"]
    if "department_ids" in data and data["department_ids"] is not None:
        r.department_ids = [str(d) for d in data["department_ids"]]
    if "cycle" in data and data["cycle"]:
        r.cycle = RotationCycle(data["cycle"]) if data["cycle"] in RotationCycle.__members__ else r.cycle
        r.frequency_days = _freq_days(data["cycle"], data.get("frequency_days") or r.frequency_days)
    elif "frequency_days" in data and data["frequency_days"]:
        r.frequency_days = max(1, int(data["frequency_days"]))
    if payload.steps is not None:
        _sync_steps(db, r, payload.steps)
    if payload.member_employee_ids is not None:
        _sync_members(db, r, payload.member_employee_ids)
    db.commit()
    db.refresh(r)
    return _rotation_response(db, r)


@router.delete("/{rotation_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_rotation(
    rotation_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRotation).filter(ShiftRotation.id == rotation_id).first()
    if not r:
        raise HTTPException(404, "Rotation not found")
    r.is_deleted = True
    r.is_active = False
    db.commit()


@router.post("/{rotation_id}/advance", response_model=RotationAdvanceResult)
def advance_rotation(
    rotation_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRotation).filter(
        ShiftRotation.id == rotation_id, ShiftRotation.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rotation not found")
    steps = sorted(r.steps, key=lambda s: s.sequence)
    members = list(r.members)
    if not steps:
        raise HTTPException(400, "Rotation has no steps to advance")
    if not members:
        raise HTTPException(400, "Rotation has no members to schedule")

    period = r.frequency_days or 7
    window_from = date.today()
    window_to = window_from + timedelta(days=period - 1)
    next_index = r.current_step_index + 1

    written = 0
    for mem in members:
        step = steps[(next_index + mem.phase_offset) % len(steps)]
        if step.shift_id is None:
            continue  # OFF block — leave the member unassigned for this window
        # idempotency — don't duplicate an identical window
        if db.query(EmployeeShiftAssignment).filter(
            EmployeeShiftAssignment.employee_id == mem.employee_id,
            EmployeeShiftAssignment.shift_id == step.shift_id,
            EmployeeShiftAssignment.effective_from == window_from,
        ).first():
            continue
        # close prior overlapping assignments on a different shift
        prior = (
            db.query(EmployeeShiftAssignment)
            .filter(EmployeeShiftAssignment.employee_id == mem.employee_id,
                    EmployeeShiftAssignment.effective_from <= window_from,
                    or_(EmployeeShiftAssignment.effective_until.is_(None),
                        EmployeeShiftAssignment.effective_until >= window_from))
            .all())
        for p in prior:
            if p.shift_id != step.shift_id:
                p.effective_until = window_from - timedelta(days=1)
        db.add(EmployeeShiftAssignment(
            employee_id=mem.employee_id, shift_id=step.shift_id,
            effective_from=window_from, effective_until=window_to,
            notes=f"Rotation · {r.name}", created_by_id=admin.id))
        written += 1

    r.current_step_index = next_index
    r.last_advanced_on = window_from
    db.flush()
    log(db, actor_id=admin.id, action=AttendanceLogAction.SHIFT_ASSIGNED,
        target_table="hr_shift_rotations", target_id=r.id,
        payload={"rotation": r.name, "advanced_to": next_index, "written": written})
    db.commit()
    return RotationAdvanceResult(
        rotation_id=r.id, advanced_to_step=next_index % len(steps),
        assignments_written=written, window_from=window_from, window_to=window_to)
