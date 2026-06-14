"""HR Weekly Rosters — draft grid of (employee × day → shift), then publish.

Publishing materialises each dated entry into a one-day
`EmployeeShiftAssignment` so the daily attendance rollup honours it. Drafts are
freely editable; published rosters are locked (re-publish is rejected).
"""
from __future__ import annotations

from datetime import date, timedelta, datetime
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.shift import Shift, EmployeeShiftAssignment
from app.models.hr.shift_roster import ShiftRoster, ShiftRosterEntry, RosterStatus
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.shift_planning import (
    ShiftRosterCreate, ShiftRosterUpdate, ShiftRosterResponse,
    RosterEntryResponse, RosterBulkEntriesBody, RosterPublishResult,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.attendance_logic import log

router = APIRouter(prefix="/hr/shift-rosters", tags=["HR — Shift Rosters"])


def _emp_names(db: Session, emp_ids) -> dict:
    ids = list({e for e in emp_ids if e})
    if not ids:
        return {}
    rows = (db.query(Employee.id, User.full_name)
            .join(User, User.id == Employee.user_id)
            .filter(Employee.id.in_(ids)).all())
    return {eid: name for eid, name in rows}


def _shift_meta(db: Session, shift_ids) -> dict:
    ids = list({s for s in shift_ids if s})
    if not ids:
        return {}
    return {s.id: s for s in db.query(Shift).filter(Shift.id.in_(ids)).all()}


def _entry_response(e: ShiftRosterEntry, names: dict, smeta: dict) -> RosterEntryResponse:
    sh = smeta.get(e.shift_id)
    return RosterEntryResponse(
        id=e.id, employee_id=e.employee_id, employee_name=names.get(e.employee_id),
        day=e.day, shift_id=e.shift_id,
        shift_code=sh.code if sh else None, shift_name=sh.name if sh else None,
        duty_hours=float(e.duty_hours) if e.duty_hours is not None else None)


def _roster_response(db: Session, r: ShiftRoster, include_entries=False) -> ShiftRosterResponse:
    dept_name = None
    if r.department_id:
        d = db.query(Department.name).filter(Department.id == r.department_id).first()
        dept_name = d[0] if d else None
    entries = []
    entry_count = db.query(func.count(ShiftRosterEntry.id)).filter(
        ShiftRosterEntry.roster_id == r.id).scalar() or 0
    if include_entries:
        rows = db.query(ShiftRosterEntry).filter(
            ShiftRosterEntry.roster_id == r.id).order_by(ShiftRosterEntry.day.asc()).all()
        names = _emp_names(db, [e.employee_id for e in rows])
        smeta = _shift_meta(db, [e.shift_id for e in rows])
        entries = [_entry_response(e, names, smeta) for e in rows]
    return ShiftRosterResponse(
        id=r.id, name=r.name, week_start=r.week_start, week_end=r.week_end,
        department_id=r.department_id, department_name=dept_name,
        status=r.status.value if hasattr(r.status, "value") else str(r.status),
        notes=r.notes, published_at=r.published_at, entry_count=entry_count,
        created_at=r.created_at, entries=entries)


@router.get("/", response_model=dict)
def list_rosters(
    status: Optional[str] = None,
    department_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(ShiftRoster).filter(ShiftRoster.is_deleted == False)  # noqa: E712
    if status:
        q = q.filter(ShiftRoster.status == status)
    if department_id:
        q = q.filter(ShiftRoster.department_id == department_id)
    total = q.count()
    rows = q.order_by(ShiftRoster.week_start.desc()).offset((page - 1) * limit).limit(limit).all()
    return {
        "items": [_roster_response(db, r) for r in rows],
        "total": total, "page": page, "limit": limit,
        "total_pages": ceil(total / limit) if limit else 1,
    }


@router.post("/", response_model=ShiftRosterResponse, status_code=http_status.HTTP_201_CREATED)
def create_roster(
    payload: ShiftRosterCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    week_end = payload.week_end or (payload.week_start + timedelta(days=6))
    if week_end < payload.week_start:
        raise HTTPException(400, "week_end must be on/after week_start")
    r = ShiftRoster(
        name=payload.name, week_start=payload.week_start, week_end=week_end,
        department_id=payload.department_id, notes=payload.notes,
        status=RosterStatus.DRAFT, created_by_id=admin.id)
    db.add(r)
    db.commit()
    db.refresh(r)
    return _roster_response(db, r, include_entries=True)


@router.get("/{roster_id}", response_model=ShiftRosterResponse)
def get_roster(
    roster_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRoster).filter(
        ShiftRoster.id == roster_id, ShiftRoster.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Roster not found")
    return _roster_response(db, r, include_entries=True)


@router.patch("/{roster_id}", response_model=ShiftRosterResponse)
def update_roster(
    roster_id: UUID,
    payload: ShiftRosterUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRoster).filter(
        ShiftRoster.id == roster_id, ShiftRoster.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Roster not found")
    if r.status == RosterStatus.PUBLISHED:
        raise HTTPException(409, "Published rosters are locked")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(r, k, v)
    if r.week_end < r.week_start:
        raise HTTPException(400, "week_end must be on/after week_start")
    db.commit()
    db.refresh(r)
    return _roster_response(db, r, include_entries=True)


@router.delete("/{roster_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_roster(
    roster_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRoster).filter(ShiftRoster.id == roster_id).first()
    if not r:
        raise HTTPException(404, "Roster not found")
    r.is_deleted = True
    db.commit()


@router.put("/{roster_id}/entries", response_model=ShiftRosterResponse)
def upsert_entries(
    roster_id: UUID,
    body: RosterBulkEntriesBody,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRoster).filter(
        ShiftRoster.id == roster_id, ShiftRoster.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Roster not found")
    if r.status == RosterStatus.PUBLISHED:
        raise HTTPException(409, "Published rosters are locked — clone to a new draft to edit")
    for ent in body.entries:
        if ent.day < r.week_start or ent.day > r.week_end:
            continue  # ignore days outside the roster window
        existing = db.query(ShiftRosterEntry).filter(
            ShiftRosterEntry.roster_id == r.id,
            ShiftRosterEntry.employee_id == ent.employee_id,
            ShiftRosterEntry.day == ent.day).first()
        if existing:
            existing.shift_id = ent.shift_id
            existing.duty_hours = ent.duty_hours
        else:
            db.add(ShiftRosterEntry(
                roster_id=r.id, employee_id=ent.employee_id, day=ent.day,
                shift_id=ent.shift_id, duty_hours=ent.duty_hours))
    db.commit()
    db.refresh(r)
    return _roster_response(db, r, include_entries=True)


@router.post("/{roster_id}/publish", response_model=RosterPublishResult)
def publish_roster(
    roster_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    r = db.query(ShiftRoster).filter(
        ShiftRoster.id == roster_id, ShiftRoster.is_deleted == False).first()  # noqa: E712
    if not r:
        raise HTTPException(404, "Roster not found")
    if r.status == RosterStatus.PUBLISHED:
        raise HTTPException(409, "Roster already published")

    entries = db.query(ShiftRosterEntry).filter(
        ShiftRosterEntry.roster_id == r.id, ShiftRosterEntry.shift_id.isnot(None)).all()
    written, skipped = 0, 0
    for e in entries:
        # idempotency — skip an identical one-day window
        if db.query(EmployeeShiftAssignment).filter(
            EmployeeShiftAssignment.employee_id == e.employee_id,
            EmployeeShiftAssignment.shift_id == e.shift_id,
            EmployeeShiftAssignment.effective_from == e.day,
            EmployeeShiftAssignment.effective_until == e.day,
        ).first():
            skipped += 1
            continue
        # Every standing assignment that covers this single day.
        covering = (db.query(EmployeeShiftAssignment)
                    .filter(EmployeeShiftAssignment.employee_id == e.employee_id,
                            EmployeeShiftAssignment.effective_from <= e.day,
                            or_(EmployeeShiftAssignment.effective_until.is_(None),
                                EmployeeShiftAssignment.effective_until >= e.day)).all())
        # Already on this shift today (via a standing assignment or rotation
        # window)? The roster adds nothing — don't write a duplicate row.
        if any(p.shift_id == e.shift_id for p in covering):
            skipped += 1
            continue
        # SPLIT each different-shift assignment AROUND this one day so the
        # employee's normal shift RESUMES the next day. This is a one-day
        # override, not a permanent truncation — coverage before and after the
        # rostered day is preserved.
        for p in covering:
            if p.shift_id == e.shift_id:
                continue
            orig_until = p.effective_until
            starts_on = p.effective_from == e.day
            ends_on = orig_until == e.day
            if starts_on and ends_on:
                db.delete(p)                                  # was a 1-day diff-shift → replaced
            elif starts_on:
                p.effective_from = e.day + timedelta(days=1)  # shave the leading day
            elif ends_on:
                p.effective_until = e.day - timedelta(days=1)  # shave the trailing day
            else:
                # the day sits strictly inside [from … until] → keep the head,
                # re-open an identical tail the day AFTER the override.
                p.effective_until = e.day - timedelta(days=1)
                db.add(EmployeeShiftAssignment(
                    employee_id=p.employee_id, shift_id=p.shift_id,
                    effective_from=e.day + timedelta(days=1), effective_until=orig_until,
                    is_default=p.is_default, notes=p.notes, created_by_id=p.created_by_id))
        db.add(EmployeeShiftAssignment(
            employee_id=e.employee_id, shift_id=e.shift_id,
            effective_from=e.day, effective_until=e.day,
            notes=f"Roster · {r.name or r.week_start.isoformat()}", created_by_id=admin.id))
        written += 1

    r.status = RosterStatus.PUBLISHED
    r.published_at = datetime.utcnow()
    r.published_by_id = admin.id
    db.flush()
    log(db, actor_id=admin.id, action=AttendanceLogAction.SHIFT_ASSIGNED,
        target_table="hr_shift_rosters", target_id=r.id,
        payload={"week_start": r.week_start.isoformat(), "written": written, "skipped": skipped})
    db.commit()
    return RosterPublishResult(roster_id=r.id, assignments_written=written, skipped=skipped)
