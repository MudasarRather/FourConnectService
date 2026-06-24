"""HR Shifts — templates + effective-dated employee assignments."""
from __future__ import annotations

from datetime import date, timedelta, datetime
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, and_, func, distinct, extract
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.department import Department
from app.models.hr.shift import Shift, EmployeeShiftAssignment, ShiftType
from app.models.hr.holiday import Holiday, HolidayType
from app.models.hr.holiday_shift import HolidayShiftAssignment
from app.models.hr.overtime import OvertimeRequest, OtStatus
from app.models.hr.shift_rotation import ShiftRotation
from app.models.hr.shift_coverage import ShiftCoverageRule
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.attendance import (
    ShiftCreate, ShiftUpdate, ShiftResponse, ShiftListResponse,
    EmployeeShiftAssignmentCreate, EmployeeShiftAssignmentResponse, ShiftAssignBulkBody,
)
from app.schemas.hr.shift_planning import (
    ShiftDashboardResponse, ShiftKpis, ShiftDistributionItem, DeptAllocationItem,
    TrendPoint, CoverageSnapshot, ShiftCalendarResponse, CalendarDay, CalendarAssignment,
)
from app.utils.dependencies import get_current_user, get_current_superuser
from app.utils.hr.attendance_logic import log
from app.utils.hr.lifecycle_guard import guard_within_tenure, guard_schedulable, SEPARATED

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
    # outerjoin User so employees without a linked login still resolve lifecycle.
    emp_row = (
        db.query(Employee.lifecycle_state, Employee.last_working_date, User.full_name)
        .outerjoin(User, User.id == Employee.user_id)
        .filter(Employee.id == a.employee_id)
        .first()
    )
    lifecycle = None
    lwd = None
    full_name = None
    if emp_row:
        ls = emp_row[0]
        lifecycle = ls.value if hasattr(ls, "value") else (str(ls) if ls is not None else None)
        lwd = emp_row[1]
        full_name = emp_row[2]
    return EmployeeShiftAssignmentResponse(
        id=a.id, employee_id=a.employee_id,
        employee_name=full_name,
        lifecycle_state=lifecycle,
        last_working_date=lwd,
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


# ── Aggregations: dashboard + calendar (literal paths — declared before /{shift_id}) ──

def _active_on(on_date: date):
    """SQLAlchemy condition: an assignment is active on `on_date`."""
    return and_(
        EmployeeShiftAssignment.effective_from <= on_date,
        or_(
            EmployeeShiftAssignment.effective_until.is_(None),
            EmployeeShiftAssignment.effective_until >= on_date,
        ),
    )


def _type_str(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


_MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@router.get("/dashboard", response_model=ShiftDashboardResponse)
def shifts_dashboard(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()
    act = _active_on(today)

    # ── KPIs ──
    active_shifts = db.query(func.count(Shift.id)).filter(
        Shift.is_deleted == False, Shift.is_active == True).scalar() or 0  # noqa: E712

    # Deployment counts only the present workforce — separated employees
    # (EXITED / ARCHIVED / INACTIVE) are no longer deployed even if a stale
    # assignment window still spans today.
    employees_assigned = (
        db.query(func.count(distinct(EmployeeShiftAssignment.employee_id)))
        .join(Employee, Employee.id == EmployeeShiftAssignment.employee_id)
        .filter(act, Employee.is_deleted == False,  # noqa: E712
                Employee.lifecycle_state.notin_(SEPARATED)).scalar() or 0)

    night_shift_employees = (
        db.query(func.count(distinct(EmployeeShiftAssignment.employee_id)))
        .join(Shift, Shift.id == EmployeeShiftAssignment.shift_id)
        .join(Employee, Employee.id == EmployeeShiftAssignment.employee_id)
        .filter(Shift.shift_type == ShiftType.NIGHT, act, Employee.is_deleted == False,  # noqa: E712
                Employee.lifecycle_state.notin_(SEPARATED)).scalar() or 0)

    upcoming_rotations = db.query(func.count(ShiftRotation.id)).filter(
        ShiftRotation.is_deleted == False, ShiftRotation.is_active == True).scalar() or 0  # noqa: E712

    month_start = today.replace(day=1)
    overtime_hours = float(
        db.query(func.coalesce(func.sum(OvertimeRequest.ot_hours), 0))
        .filter(OvertimeRequest.is_deleted == False,  # noqa: E712
                OvertimeRequest.status == OtStatus.APPROVED,
                OvertimeRequest.date >= month_start).scalar() or 0)

    next_holiday = (
        db.query(Holiday)
        .filter(Holiday.is_deleted == False, Holiday.is_active == True,  # noqa: E712
                Holiday.date >= today)
        .order_by(Holiday.date.asc()).first())
    holiday_shift_staff = 0
    if next_holiday:
        # Staff genuinely working the next holiday = explicit holiday-shift
        # overrides, NOT everyone whose regular assignment window happens to
        # span the date. A holiday rests the workforce by default (matching the
        # calendar + attendance rollup); only Holiday Shift rostered staff work.
        holiday_shift_staff = db.query(
            func.count(distinct(HolidayShiftAssignment.employee_id))
        ).filter(HolidayShiftAssignment.holiday_id == next_holiday.id,
                 HolidayShiftAssignment.is_deleted == False).scalar() or 0  # noqa: E712

    # conflicts — employees holding >1 active assignment today
    dupes = (
        db.query(EmployeeShiftAssignment.employee_id)
        .filter(act)
        .group_by(EmployeeShiftAssignment.employee_id)
        .having(func.count(EmployeeShiftAssignment.id) > 1).all())
    shift_conflicts = len(dupes)

    total_active_emp = db.query(func.count(Employee.id)).filter(
        Employee.is_deleted == False,  # noqa: E712
        Employee.lifecycle_state == LifecycleState.ACTIVE).scalar() or 0
    assigned_active = (
        db.query(func.count(distinct(Employee.id)))
        .join(EmployeeShiftAssignment, EmployeeShiftAssignment.employee_id == Employee.id)
        .filter(Employee.is_deleted == False,  # noqa: E712
                Employee.lifecycle_state == LifecycleState.ACTIVE, act).scalar() or 0)
    unassigned_employees = max(0, total_active_emp - assigned_active)

    kpis = ShiftKpis(
        active_shifts=active_shifts, employees_assigned=employees_assigned,
        night_shift_employees=night_shift_employees, upcoming_rotations=upcoming_rotations,
        holiday_shift_staff=holiday_shift_staff, overtime_hours=round(overtime_hours, 2),
        shift_conflicts=shift_conflicts, unassigned_employees=unassigned_employees,
    )

    # ── Shift distribution (assignments per active shift, today) ──
    # Count distinct present-workforce employees per shift (separated crew excluded
    # via the Employee outerjoin's ON clause, so empty shifts still report 0).
    dist_rows = (
        db.query(Shift, func.count(distinct(Employee.id)))
        .outerjoin(EmployeeShiftAssignment,
                   and_(EmployeeShiftAssignment.shift_id == Shift.id, _active_on(today)))
        .outerjoin(Employee,
                   and_(Employee.id == EmployeeShiftAssignment.employee_id,
                        Employee.is_deleted == False,  # noqa: E712
                        Employee.lifecycle_state.notin_(SEPARATED)))
        .filter(Shift.is_deleted == False, Shift.is_active == True)  # noqa: E712
        .group_by(Shift.id).order_by(Shift.created_at.asc()).all())
    shift_distribution = [
        ShiftDistributionItem(shift_id=s.id, code=s.code, name=s.name,
                              shift_type=_type_str(s.shift_type), count=c or 0)
        for s, c in dist_rows]

    # ── Department allocation ──
    dept_total = dict(
        db.query(Employee.department_id, func.count(distinct(EmployeeShiftAssignment.employee_id)))
        .join(EmployeeShiftAssignment, EmployeeShiftAssignment.employee_id == Employee.id)
        .filter(act, Employee.is_deleted == False,  # noqa: E712
                Employee.lifecycle_state.notin_(SEPARATED))
        .group_by(Employee.department_id).all())
    dept_night = dict(
        db.query(Employee.department_id, func.count(distinct(EmployeeShiftAssignment.employee_id)))
        .join(EmployeeShiftAssignment, EmployeeShiftAssignment.employee_id == Employee.id)
        .join(Shift, Shift.id == EmployeeShiftAssignment.shift_id)
        .filter(act, Shift.shift_type == ShiftType.NIGHT, Employee.is_deleted == False,  # noqa: E712
                Employee.lifecycle_state.notin_(SEPARATED))
        .group_by(Employee.department_id).all())
    dept_names = dict(db.query(Department.id, Department.name).all())
    dept_allocation = [
        DeptAllocationItem(
            department_id=did,
            department_name=(dept_names.get(did) if did else None) or "Unassigned",
            count=cnt or 0, night_count=dept_night.get(did, 0))
        for did, cnt in dept_total.items()]
    dept_allocation.sort(key=lambda d: d.count, reverse=True)

    # ── Overtime trend (last 6 months, approved) ──
    buckets, y, m = [], today.year, today.month
    for _ in range(6):
        buckets.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    buckets.reverse()
    range_start = date(buckets[0][0], buckets[0][1], 1)
    ot_map = {}
    for yy, mm, tot in (
        db.query(extract("year", OvertimeRequest.date),
                 extract("month", OvertimeRequest.date),
                 func.coalesce(func.sum(OvertimeRequest.ot_hours), 0))
        .filter(OvertimeRequest.is_deleted == False,  # noqa: E712
                OvertimeRequest.status == OtStatus.APPROVED,
                OvertimeRequest.date >= range_start)
        .group_by(extract("year", OvertimeRequest.date),
                  extract("month", OvertimeRequest.date)).all()):
        ot_map[(int(yy), int(mm))] = float(tot or 0)
    overtime_trend = [TrendPoint(label=_MONTHS[mm], value=round(ot_map.get((yy, mm), 0.0), 1))
                      for yy, mm in buckets]

    # ── Night utilisation (last 7 days) ──
    window_start = today - timedelta(days=6)
    night_assigns = (
        db.query(EmployeeShiftAssignment.effective_from, EmployeeShiftAssignment.effective_until)
        .join(Shift, Shift.id == EmployeeShiftAssignment.shift_id)
        .join(Employee, Employee.id == EmployeeShiftAssignment.employee_id)
        .filter(Shift.shift_type == ShiftType.NIGHT,
                Employee.is_deleted == False,  # noqa: E712
                Employee.lifecycle_state.notin_(SEPARATED),
                EmployeeShiftAssignment.effective_from <= today,
                or_(EmployeeShiftAssignment.effective_until.is_(None),
                    EmployeeShiftAssignment.effective_until >= window_start)).all())
    night_utilization = []
    for i in range(7):
        d = window_start + timedelta(days=i)
        cnt = sum(1 for ef, eu in night_assigns if ef <= d and (eu is None or eu >= d))
        night_utilization.append(TrendPoint(label=_DOW[d.weekday()], value=float(cnt)))

    # ── Weekly coverage (from active coverage rules) ──
    rules = (db.query(ShiftCoverageRule)
             .filter(ShiftCoverageRule.is_deleted == False,  # noqa: E712
                     ShiftCoverageRule.is_active == True)
             .order_by(ShiftCoverageRule.created_at.asc()).limit(12).all())
    weekly_coverage = []
    for r in rules:
        cq = (db.query(func.count(distinct(EmployeeShiftAssignment.employee_id)))
              .join(Employee, Employee.id == EmployeeShiftAssignment.employee_id)
              .filter(EmployeeShiftAssignment.shift_id == r.shift_id, _active_on(today),
                      Employee.is_deleted == False,  # noqa: E712
                      Employee.lifecycle_state.notin_(SEPARATED)))
        if r.department_id:
            cq = cq.filter(Employee.department_id == r.department_id)
        assigned = cq.scalar() or 0
        sh = db.query(Shift.name).filter(Shift.id == r.shift_id).first()
        weekly_coverage.append(CoverageSnapshot(
            label=r.label or (sh[0] if sh else "Shift"),
            required=r.min_staff or 0, assigned=assigned))

    return ShiftDashboardResponse(
        kpis=kpis, shift_distribution=shift_distribution, dept_allocation=dept_allocation,
        overtime_trend=overtime_trend, night_utilization=night_utilization,
        weekly_coverage=weekly_coverage, generated_at=datetime.utcnow(),
    )


@router.get("/calendar", response_model=ShiftCalendarResponse)
def shifts_calendar(
    from_: date = Query(..., alias="from"),
    to: date = Query(..., alias="to"),
    department_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if to < from_:
        raise HTTPException(400, "`to` must be on or after `from`")
    if (to - from_).days > 92:
        raise HTTPException(400, "Range too large (max 92 days)")

    q = (
        db.query(EmployeeShiftAssignment, Shift, User.full_name, Employee.work_location_id)
        .join(Shift, Shift.id == EmployeeShiftAssignment.shift_id)
        .join(Employee, Employee.id == EmployeeShiftAssignment.employee_id)
        .join(User, User.id == Employee.user_id)
        .filter(Shift.is_deleted == False,  # noqa: E712 — don't roster deleted shifts
                Employee.is_deleted == False,  # noqa: E712 — or exited/deleted employees
                EmployeeShiftAssignment.effective_from <= to,
                or_(EmployeeShiftAssignment.effective_until.is_(None),
                    EmployeeShiftAssignment.effective_until >= from_)))
    if department_id:
        q = q.filter(Employee.department_id == department_id)
    rows = q.all()

    # Active holidays in range that actually rest the workforce. RESTRICTED
    # (optional/floating) holidays are claimed per-employee, not company-wide,
    # so — exactly like the attendance daily rollup (attendance_logic.py) — we
    # exclude them here so they neither show a calendar-wide badge nor suppress
    # shifts. A single date can carry more than one holiday (e.g. a company-wide
    # one plus a location-specific one), so we keep the full list per date and
    # resolve the location match at the individual-employee level below.
    hol_rows = db.query(Holiday).filter(
        Holiday.is_deleted == False, Holiday.is_active == True,  # noqa: E712
        Holiday.holiday_type != HolidayType.RESTRICTED,
        Holiday.date >= from_, Holiday.date <= to).all()
    hol_by_date: dict = {}
    for h in hol_rows:
        hol_by_date.setdefault(h.date, []).append(h)

    # Explicit holiday-shift overrides: employees rostered to ACTUALLY work a
    # given holiday (with a comp rule, via the Holiday Shifts register). These
    # stay counted as "on shift" instead of being moved to the holiday-rest
    # bucket. Keyed by the holiday's date.
    override_by_date: dict = {}
    for hsa_emp_id, hsa_date in (
        db.query(HolidayShiftAssignment.employee_id, Holiday.date)
        .join(Holiday, Holiday.id == HolidayShiftAssignment.holiday_id)
        .filter(HolidayShiftAssignment.is_deleted == False,  # noqa: E712
                Holiday.is_deleted == False,  # noqa: E712
                Holiday.date >= from_, Holiday.date <= to).all()):
        override_by_date.setdefault(hsa_date, set()).add(hsa_emp_id)

    def _holiday_name_for_day(hs: list):
        # Prefer a company-wide holiday's name for the day badge; fall back to
        # the first location-specific one.
        for h in hs:
            if h.location_id is None:
                return h.name
        return hs[0].name if hs else None

    days = []
    d = from_
    while d <= to:
        day_assigns = []
        day_off = []
        day_hol_off = []
        day_hols = hol_by_date.get(d, [])
        overrides = override_by_date.get(d, set())
        for a, s, name, work_location_id in rows:
            if a.effective_from <= d and (a.effective_until is None or a.effective_until >= d):
                ca = CalendarAssignment(
                    employee_id=a.employee_id, employee_name=name, shift_id=s.id,
                    shift_code=s.code, shift_name=s.name, shift_type=_type_str(s.shift_type),
                    start_time=s.start_time, end_time=s.end_time)
                # A holiday rests this employee only if it applies to them:
                # company-wide (location_id is None) OR their own work location.
                emp_on_holiday = any(
                    h.location_id is None or h.location_id == work_location_id
                    for h in day_hols)
                if emp_on_holiday and a.employee_id not in overrides:
                    # Holiday wins over weekly-off for the rest classification,
                    # matching attendance (holiday short-circuits before week-off).
                    day_hol_off.append(ca)
                elif s.weekly_off_days and d.weekday() in (s.weekly_off_days or []):
                    day_off.append(ca)  # assigned, but it's their weekly-off — resting
                else:
                    # Normal working day, or an explicit holiday-shift worker.
                    day_assigns.append(ca)
        days.append(CalendarDay(
            date=d, weekday=d.weekday(),
            is_holiday=bool(day_hols), holiday_name=_holiday_name_for_day(day_hols),
            assignments=day_assigns, count=len(day_assigns),
            week_off=day_off, week_off_count=len(day_off),
            holiday_off=day_hol_off, holiday_off_count=len(day_hol_off)))
        d += timedelta(days=1)

    return ShiftCalendarResponse(from_date=from_, to_date=to, days=days)


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
    upcoming: bool = False,
    include_separated: bool = False,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    # The "Active deployments" board shows who is actually on the roster. A
    # separated employee (EXITED / ARCHIVED / INACTIVE) is no longer active crew
    # regardless of an assignment window that may still be open or future-dated,
    # so they are excluded by default. ON_NOTICE crew are still working until
    # their last day and DO appear (flagged via lifecycle_state in the response).
    # `include_separated=true` is the escape hatch for an audit/all view.
    q = db.query(EmployeeShiftAssignment).join(
        Employee, Employee.id == EmployeeShiftAssignment.employee_id
    ).filter(Employee.is_deleted == False)  # noqa: E712
    if not include_separated:
        q = q.filter(Employee.lifecycle_state.notin_(SEPARATED))
    if employee_id:
        q = q.filter(EmployeeShiftAssignment.employee_id == employee_id)
    if shift_id:
        q = q.filter(EmployeeShiftAssignment.shift_id == shift_id)
    if active_on:
        # Default: assignments active *on* the date. With upcoming=True, also
        # include windows that start after the date (current + future) so a
        # rotation's later-week shifts show as scheduled before they begin.
        if not upcoming:
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
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    if not db.query(Shift).filter(Shift.id == payload.shift_id, Shift.is_deleted == False).first():  # noqa: E712
        raise HTTPException(404, "Shift not found")
    # A separated employee can't be assigned at all; a leaving one not past their LWD.
    guard_schedulable(emp, payload.effective_from, "assign a shift")
    if payload.effective_until:
        guard_schedulable(emp, payload.effective_until, "assign a shift")
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
    # Also block when employees still have this as their DEFAULT shift — deleting
    # it would leave a dangling Employee.shift_id, and resolve_shift() then returns
    # None → those employees get marked ABSENT every day (breaks attendance + pay).
    default_of = (
        db.query(Employee)
        .filter(Employee.shift_id == shift_id, Employee.is_deleted == False)  # noqa: E712
        .count()
    )
    if default_of:
        raise HTTPException(409, f"Cannot delete; it is the default shift of {default_of} employee(s). Reassign their shift first.")
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
    if not target_shift.is_active:
        raise HTTPException(409, "Shift is archived; reactivate it before assigning crew.")

    new_from = body.effective_from
    new_until = body.effective_until  # may be None for indefinite

    # Pre-flight 0: lifecycle / tenure. A leaving or departed employee may not be
    # deployed past their last working day (and the fully-separated not at all).
    # Mirrors create_assignment's guard; collected so we can name everyone at once.
    blocked = []
    for emp_id in body.employee_ids:
        emp = db.query(Employee).filter(Employee.id == emp_id, Employee.is_deleted == False).first()  # noqa: E712
        if not emp:
            continue
        try:
            guard_schedulable(emp, new_from, "deploy to a shift")
            if new_until is not None:
                guard_schedulable(emp, new_until, "deploy to a shift")
        except HTTPException as exc:
            name_row = (
                db.query(User.full_name)
                .join(Employee, Employee.user_id == User.id)
                .filter(Employee.id == emp_id).first()
            )
            blocked.append({
                "employee_id": str(emp_id),
                "employee_name": (name_row[0] if name_row else None) or emp.employee_id,
                "lifecycle_state": emp.lifecycle_state.value if hasattr(emp.lifecycle_state, "value") else str(emp.lifecycle_state),
                "last_working_date": emp.last_working_date.isoformat() if emp.last_working_date else None,
                "reason": exc.detail,
            })
    if blocked:
        names = ", ".join(sorted({b["employee_name"] for b in blocked}))
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Cannot deploy {names}: they have left or are serving notice and the "
                    "shift window runs past their last working day. Shorten the window or remove them."
                ),
                "blocked": blocked,
            },
        )

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
