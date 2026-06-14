"""HR Workforce Planning — demand CRUD + day-by-day staffing forecast.

The forecast projects each active demand against assigned capacity across a
date window, surfacing shortfalls before they happen. Reuses the assignment
engine; honours each shift's weekly-off days (an employee on their off day is
not counted as on-shift).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.department import Department
from app.models.hr.shift import Shift, EmployeeShiftAssignment
from app.models.hr.workforce_demand import WorkforceDemand
from app.models.hr.leave_request import LeaveRequest
from app.models.hr.leave_type import LeaveStatus
from app.models.hr.holiday import Holiday, HolidayType
from app.models.hr.holiday_shift import HolidayShiftAssignment
from app.schemas.hr.workforce import (
    WorkforceDemandCreate, WorkforceDemandUpdate, WorkforceDemandResponse,
    ForecastCell, ForecastDay, WorkforceForecastSummary, WorkforceForecastResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/workforce", tags=["HR — Workforce Planning"])


def _resp(db: Session, d: WorkforceDemand) -> WorkforceDemandResponse:
    sh = db.query(Shift).filter(Shift.id == d.shift_id).first()
    dept_name = None
    if d.department_id:
        row = db.query(Department.name).filter(Department.id == d.department_id).first()
        dept_name = row[0] if row else None
    return WorkforceDemandResponse(
        id=d.id, shift_id=d.shift_id, shift_code=sh.code if sh else None, shift_name=sh.name if sh else None,
        department_id=d.department_id, department_name=dept_name,
        required_headcount=d.required_headcount, skill=d.skill,
        valid_from=d.valid_from, valid_to=d.valid_to, notes=d.notes,
        is_active=d.is_active, created_at=d.created_at)


# ── Demand CRUD ──

@router.get("/demands", response_model=dict)
def list_demands(
    is_active: Optional[bool] = None,
    department_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(WorkforceDemand).filter(WorkforceDemand.is_deleted == False)  # noqa: E712
    if is_active is not None:
        q = q.filter(WorkforceDemand.is_active == is_active)
    if department_id:
        q = q.filter(WorkforceDemand.department_id == department_id)
    total = q.count()
    rows = q.order_by(WorkforceDemand.valid_from.desc()).offset((page - 1) * limit).limit(limit).all()
    return {"items": [_resp(db, d) for d in rows], "total": total, "page": page, "limit": limit,
            "total_pages": ceil(total / limit) if limit else 1}


@router.post("/demands", response_model=WorkforceDemandResponse, status_code=http_status.HTTP_201_CREATED)
def create_demand(payload: WorkforceDemandCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if not db.query(Shift).filter(Shift.id == payload.shift_id, Shift.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Shift not found")
    if payload.valid_to and payload.valid_to < payload.valid_from:
        raise HTTPException(400, "valid_to must be on/after valid_from")
    d = WorkforceDemand(
        shift_id=payload.shift_id, department_id=payload.department_id,
        required_headcount=max(0, payload.required_headcount), skill=payload.skill,
        valid_from=payload.valid_from, valid_to=payload.valid_to, notes=payload.notes,
        created_by_id=admin.id)
    db.add(d)
    db.commit()
    db.refresh(d)
    return _resp(db, d)


@router.patch("/demands/{demand_id}", response_model=WorkforceDemandResponse)
def update_demand(demand_id: UUID, payload: WorkforceDemandUpdate, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    d = db.query(WorkforceDemand).filter(WorkforceDemand.id == demand_id, WorkforceDemand.is_deleted == False).first()  # noqa: E712
    if not d:
        raise HTTPException(404, "Demand not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(d, k, v)
    if d.valid_to and d.valid_to < d.valid_from:
        raise HTTPException(400, "valid_to must be on/after valid_from")
    db.commit()
    db.refresh(d)
    return _resp(db, d)


@router.delete("/demands/{demand_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_demand(demand_id: UUID, db: Session = Depends(get_db), _admin: User = Depends(get_current_superuser)):
    d = db.query(WorkforceDemand).filter(WorkforceDemand.id == demand_id).first()
    if not d:
        raise HTTPException(404, "Demand not found")
    d.is_deleted = True
    db.commit()


# ── Forecast ──

@router.get("/forecast", response_model=WorkforceForecastResponse)
def forecast(
    from_: date = Query(..., alias="from"),
    to: date = Query(..., alias="to"),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if to < from_:
        raise HTTPException(400, "`to` must be on or after `from`")
    if (to - from_).days > 62:
        raise HTTPException(400, "Range too large (max 62 days)")

    dq = (db.query(WorkforceDemand)
          .filter(WorkforceDemand.is_deleted == False, WorkforceDemand.is_active == True,  # noqa: E712
                  WorkforceDemand.valid_from <= to,
                  or_(WorkforceDemand.valid_to.is_(None), WorkforceDemand.valid_to >= from_)))
    if department_id:
        dq = dq.filter(WorkforceDemand.department_id == department_id)
    demands = dq.all()

    # shift meta + assignment rows overlapping the window
    shift_meta = {s.id: s for s in db.query(Shift).all()}
    assigns = (
        db.query(EmployeeShiftAssignment.employee_id, EmployeeShiftAssignment.shift_id,
                 EmployeeShiftAssignment.effective_from, EmployeeShiftAssignment.effective_until,
                 Employee.department_id, Employee.work_location_id)
        .join(Employee, Employee.id == EmployeeShiftAssignment.employee_id)
        .filter(EmployeeShiftAssignment.effective_from <= to,
                or_(EmployeeShiftAssignment.effective_until.is_(None),
                    EmployeeShiftAssignment.effective_until >= from_)).all())

    # Approved FULL-DAY leave overlapping the window. An assigned employee on
    # leave is NOT available capacity that day — mirrors the attendance rollup,
    # where full-day APPROVED leave overrides presence (attendance_logic.py).
    # Half-day leave still counts as a head. Without this the forecast
    # over-states coverage and hides real shortfalls.
    on_leave: set = set()
    leave_rows = (
        db.query(LeaveRequest.employee_id, LeaveRequest.from_date, LeaveRequest.to_date)
        .filter(LeaveRequest.status == LeaveStatus.APPROVED,
                LeaveRequest.is_deleted == False,  # noqa: E712
                LeaveRequest.is_half_day == False,  # noqa: E712
                LeaveRequest.from_date <= to,
                LeaveRequest.to_date >= from_).all())
    for _emp_id, _lf, _lt in leave_rows:
        _d = max(_lf, from_)
        _end = min(_lt, to)
        while _d <= _end:
            on_leave.add((_emp_id, _d))
            _d += timedelta(days=1)

    # Active, non-RESTRICTED holidays rest the workforce — mirrors the shift
    # calendar + attendance rollup. RESTRICTED = optional/floating (claimed
    # per-employee), so excluded. A date may carry several holidays (company-wide
    # + location-specific); the per-employee location match below resolves which
    # apply. A HolidayShiftAssignment override keeps an employee on-shift.
    hol_by_date: dict = defaultdict(list)
    for _h in (db.query(Holiday).filter(
            Holiday.is_deleted == False, Holiday.is_active == True,  # noqa: E712
            Holiday.holiday_type != HolidayType.RESTRICTED,
            Holiday.date >= from_, Holiday.date <= to).all()):
        hol_by_date[_h.date].append(_h)
    hol_override_by_date: dict = defaultdict(set)
    for _ovr_emp, _ovr_date in (
        db.query(HolidayShiftAssignment.employee_id, Holiday.date)
        .join(Holiday, Holiday.id == HolidayShiftAssignment.holiday_id)
        .filter(HolidayShiftAssignment.is_deleted == False,  # noqa: E712
                Holiday.is_deleted == False,  # noqa: E712
                Holiday.date >= from_, Holiday.date <= to).all()):
        hol_override_by_date[_ovr_date].add(_ovr_emp)

    def _holiday_name_for(hs):
        # Prefer a company-wide holiday's name; fall back to the first one.
        for h in hs:
            if h.location_id is None:
                return h.name
        return hs[0].name if hs else None

    def label_for(d):
        sh = shift_meta.get(d.shift_id)
        base = sh.name if sh else "Shift"
        if d.skill:
            base += f" · {d.skill}"
        return base

    days = []
    shortfall_by_demand = defaultdict(int)
    tot_req = tot_assigned = tot_short = shortfall_days = 0

    cur = from_
    while cur <= to:
        cells = []
        day_req = day_assigned = day_short = 0
        day_hols = hol_by_date.get(cur, [])
        day_overrides = hol_override_by_date.get(cur, set())
        for d in demands:
            if d.valid_from > cur or (d.valid_to and d.valid_to < cur):
                continue
            sh = shift_meta.get(d.shift_id)
            if sh and sh.weekly_off_days and cur.weekday() in (sh.weekly_off_days or []):
                continue  # shift not operating this weekday
            # assigned: distinct employees active on this shift (+dept) on `cur`
            emp_set = set()
            for emp_id, shid, ef, eu, edept, ewloc in assigns:
                if shid != d.shift_id:
                    continue
                if ef > cur or (eu is not None and eu < cur):
                    continue
                if d.department_id and edept != d.department_id:
                    continue
                if (emp_id, cur) in on_leave:
                    continue  # on approved full-day leave → not available capacity
                # A holiday rests this employee unless it doesn't apply to their
                # location, or they hold a holiday-shift override for the day.
                if day_hols and emp_id not in day_overrides and any(
                        h.location_id is None or h.location_id == ewloc for h in day_hols):
                    continue
                emp_set.add(emp_id)
            assigned = len(emp_set)
            required = d.required_headcount or 0
            short = max(0, required - assigned)
            cells.append(ForecastCell(
                demand_id=d.id, label=label_for(d), shift_id=d.shift_id,
                shift_code=sh.code if sh else None, shift_name=sh.name if sh else None,
                required=required, assigned=assigned, shortfall=short,
                ratio=round(assigned / required, 2) if required else 1.0))
            day_req += required
            day_assigned += assigned
            day_short += short
            shortfall_by_demand[label_for(d)] += short
        days.append(ForecastDay(date=cur, weekday=cur.weekday(), required=day_req,
                                assigned=day_assigned, shortfall=day_short,
                                is_holiday=bool(day_hols), holiday_name=_holiday_name_for(day_hols),
                                cells=cells))
        tot_req += day_req
        tot_assigned += day_assigned
        tot_short += day_short
        if day_short > 0:
            shortfall_days += 1
        cur += timedelta(days=1)

    worst = max(shortfall_by_demand.items(), key=lambda kv: kv[1], default=(None, 0))
    summary = WorkforceForecastSummary(
        horizon_days=len(days), demand_entries=len(demands),
        total_required=tot_req, total_assigned=tot_assigned, total_shortfall=tot_short,
        shortfall_days=shortfall_days,
        coverage_pct=round((tot_assigned / tot_req) * 100, 1) if tot_req else 100.0,
        worst_shift=worst[0] if worst[1] > 0 else None,
        worst_shift_shortfall=worst[1] if worst[1] > 0 else 0)

    return WorkforceForecastResponse(from_date=from_, to_date=to, summary=summary, days=days)
