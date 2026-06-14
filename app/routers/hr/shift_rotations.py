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
    # Current step is derived from the calendar: how many periods have elapsed
    # since the anchor. This keeps the orbit highlighting the step that is
    # actually in effect today, independent of any stored cursor.
    cur_idx = r.current_step_index or 0
    if steps and r.anchor_date:
        period = r.frequency_days or 7
        today = date.today()
        if today >= r.anchor_date and period > 0:
            cur_idx = (today - r.anchor_date).days // period
    cur_label = None
    if steps:
        cs = steps[cur_idx % len(steps)]
        cur_label = cs.label or (smeta.get(cs.shift_id).name if smeta.get(cs.shift_id) else "Off")
    return ShiftRotationResponse(
        id=r.id, name=r.name, code=r.code,
        cycle=r.cycle.value if hasattr(r.cycle, "value") else str(r.cycle),
        frequency_days=r.frequency_days, description=r.description,
        department_ids=r.department_ids or [], anchor_date=r.anchor_date,
        current_step_index=cur_idx, last_advanced_on=r.last_advanced_on,
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


def _rotation_note(r: ShiftRotation) -> str:
    return f"Rotation · {r.name}"


def _materialize_cycle(db: Session, r: ShiftRotation, actor_id, cycle_no: int) -> int:
    """Write one full N-step cycle of the rotation into concrete shift
    assignments, anchored at ``anchor_date``.

    For cycle ``c`` and step-slot ``k`` (0-based), each member is placed on
    ``steps[(c*N + k + phase_offset) % N]`` for the window
    ``[anchor + (c*N+k)*period, … + period-1]``.  OFF steps (no shift) leave a
    gap.  Idempotent on (employee, shift, effective_from); past windows are
    skipped so we never backfill history; assignments on a *different* shift
    that overlap a window are trimmed so the rotation stays authoritative.
    """
    steps = db.query(ShiftRotationStep).filter(
        ShiftRotationStep.rotation_id == r.id).order_by(ShiftRotationStep.sequence).all()
    members = db.query(ShiftRotationMember).filter(
        ShiftRotationMember.rotation_id == r.id).all()
    if not steps or not members:
        return 0
    n = len(steps)
    period = r.frequency_days or 7
    anchor = r.anchor_date or date.today()
    today = date.today()
    written = 0
    for k in range(n):
        gweek = cycle_no * n + k
        wf = anchor + timedelta(days=gweek * period)
        wt = wf + timedelta(days=period - 1)
        if wt < today:
            continue  # don't materialise windows entirely in the past
        for mem in members:
            step = steps[(gweek + (mem.phase_offset or 0)) % n]
            if step.shift_id is None:
                continue  # OFF — rest block
            exists = db.query(EmployeeShiftAssignment).filter(
                EmployeeShiftAssignment.employee_id == mem.employee_id,
                EmployeeShiftAssignment.shift_id == step.shift_id,
                EmployeeShiftAssignment.effective_from == wf).first()
            if exists:
                continue
            # trim any overlapping assignment on a *different* shift
            overlapping = (
                db.query(EmployeeShiftAssignment)
                .filter(EmployeeShiftAssignment.employee_id == mem.employee_id,
                        EmployeeShiftAssignment.shift_id != step.shift_id,
                        EmployeeShiftAssignment.effective_from <= wt,
                        or_(EmployeeShiftAssignment.effective_until.is_(None),
                            EmployeeShiftAssignment.effective_until >= wf))
                .all())
            for p in overlapping:
                if p.effective_from < wf:
                    p.effective_until = wf - timedelta(days=1)
                elif p.effective_until is not None and p.effective_until <= wt:
                    db.delete(p)  # fully inside this window
                else:
                    p.effective_from = wt + timedelta(days=1)  # starts inside, extends past
            db.add(EmployeeShiftAssignment(
                employee_id=mem.employee_id, shift_id=step.shift_id,
                effective_from=wf, effective_until=wt,
                notes=_rotation_note(r), created_by_id=actor_id))
            written += 1
    return written


def _next_cycle(db: Session, r: ShiftRotation) -> int:
    """The next un-materialised cycle index — used by ``advance`` to roll the
    schedule forward one full cycle at a time."""
    steps = db.query(ShiftRotationStep).filter(ShiftRotationStep.rotation_id == r.id).all()
    n = len(steps) or 1
    period = r.frequency_days or 7
    anchor = r.anchor_date or date.today()
    member_ids = [m.employee_id for m in
                  db.query(ShiftRotationMember).filter(ShiftRotationMember.rotation_id == r.id).all()]
    if not member_ids:
        return 0
    latest = (db.query(func.max(EmployeeShiftAssignment.effective_from))
              .filter(EmployeeShiftAssignment.employee_id.in_(member_ids),
                      EmployeeShiftAssignment.notes == _rotation_note(r)).scalar())
    if not latest:
        return 0
    gweek = max(0, (latest - anchor).days // period)
    return gweek // n + 1


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
    db.flush()
    # Materialise the first full cycle from the anchor date so the schedule is
    # live immediately — no manual "advance" required to see week-N shifts.
    _materialize_cycle(db, r, admin.id, 0)
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
    db.flush()
    # Re-materialise: drop this rotation's *future* generated assignments (so a
    # changed pattern/anchor doesn't leave stale rows) and re-write the cycle.
    member_ids = [m.employee_id for m in
                  db.query(ShiftRotationMember).filter(ShiftRotationMember.rotation_id == r.id).all()]
    if member_ids:
        (db.query(EmployeeShiftAssignment)
         .filter(EmployeeShiftAssignment.employee_id.in_(member_ids),
                 EmployeeShiftAssignment.notes == _rotation_note(r),
                 EmployeeShiftAssignment.effective_from > date.today())
         .delete(synchronize_session=False))
    _materialize_cycle(db, r, _admin.id, 0)
    db.commit()
    db.refresh(r)
    return _rotation_response(db, r)


@router.get("/{rotation_id}/impact", response_model=dict)
def rotation_impact(
    rotation_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Pre-delete impact summary so the UI can warn before destroying a
    rotation: how many members ride it and how many not-yet-started shift
    assignments it has generated (the rows a "cancel upcoming" delete removes)."""
    r = db.query(ShiftRotation).filter(
        ShiftRotation.id == rotation_id, ShiftRotation.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rotation not found")
    member_ids = [m.employee_id for m in r.members]
    future = 0
    if member_ids:
        future = (db.query(EmployeeShiftAssignment)
                  .filter(EmployeeShiftAssignment.employee_id.in_(member_ids),
                          EmployeeShiftAssignment.notes == _rotation_note(r),
                          EmployeeShiftAssignment.effective_from > date.today())
                  .count())
    return {"member_count": len(member_ids), "future_assignments": future, "is_active": bool(r.is_active)}


@router.delete("/{rotation_id}", response_model=dict)
def delete_rotation(
    rotation_id: UUID,
    revoke_future: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Soft-delete a rotation. By default the shifts it already scheduled are
    KEPT (real commitments on employees' calendars) — the rotation just stops
    generating new ones. With ``revoke_future=true`` the not-yet-started
    generated assignments are also cancelled. Always audit-logged."""
    r = db.query(ShiftRotation).filter(
        ShiftRotation.id == rotation_id, ShiftRotation.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Rotation not found")

    member_ids = [m.employee_id for m in r.members]
    revoked = 0
    if revoke_future and member_ids:
        future = (db.query(EmployeeShiftAssignment)
                  .filter(EmployeeShiftAssignment.employee_id.in_(member_ids),
                          EmployeeShiftAssignment.notes == _rotation_note(r),
                          EmployeeShiftAssignment.effective_from > date.today())
                  .all())
        revoked = len(future)
        for a in future:
            db.delete(a)

    r.is_deleted = True
    r.is_active = False
    log(db, actor_id=admin.id, action=AttendanceLogAction.POLICY_CHANGE,
        target_table="hr_shift_rotations", target_id=r.id,
        payload={"event": "rotation_deleted", "rotation": r.name,
                 "members": len(member_ids), "revoke_future": revoke_future,
                 "future_assignments_revoked": revoked})
    db.commit()
    return {"deleted": True, "future_assignments_revoked": revoked}


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

    n = len(steps)
    period = r.frequency_days or 7
    anchor = r.anchor_date or date.today()
    # Roll the schedule forward by one full cycle (the next un-materialised one).
    cycle_no = _next_cycle(db, r)
    written = _materialize_cycle(db, r, admin.id, cycle_no)
    window_from = anchor + timedelta(days=cycle_no * n * period)
    window_to = anchor + timedelta(days=((cycle_no + 1) * n * period) - 1)

    r.last_advanced_on = date.today()
    db.flush()
    log(db, actor_id=admin.id, action=AttendanceLogAction.SHIFT_ASSIGNED,
        target_table="hr_shift_rotations", target_id=r.id,
        payload={"rotation": r.name, "cycle": cycle_no, "written": written})
    db.commit()
    return RotationAdvanceResult(
        rotation_id=r.id, advanced_to_step=cycle_no % n,
        assignments_written=written, window_from=window_from, window_to=window_to)
