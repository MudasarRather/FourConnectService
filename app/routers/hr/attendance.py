"""HR Attendance — admin dashboard + daily + user self-service /me endpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, time as dtime
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.shift import Shift
from app.models.hr.attendance import Attendance, AttendanceStatus, AttendanceSource
from app.models.hr.attendance_punch import AttendancePunch, PunchType
from app.models.hr.attendance_correction import AttendanceCorrection, CorrectionStatus
from app.models.hr.wfh_request import WfhRequest, WfhStatus, WfhRequestType
from app.models.hr.half_day_request import HalfDayRequest, HalfDayStatus, HalfDayWhich
from app.models.hr.holiday import Holiday
from app.models.hr.biometric_device import BiometricDevice, BiometricDeviceStatus
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.attendance import (
    AttendanceResponse, AttendanceListResponse, AttendanceUpdate, AttendanceCreateManual,
    AttendanceDashboardStats, DashboardByDeptResponse, DeptAttendance,
    HeatmapResponse, HeatmapCell,
    PunchCreate, PunchResponse, PunchListResponse,
    MeTodayResponse, MyHistoryResponse, MyHistoryDay, MyMonthResponse, MyMonthCell,
    ShiftResponse, CurrentBreakWindow,
    LatePunchRequestCreate, LatePunchRequestResponse,
    EarlyExitRequestCreate, EarlyExitRequestResponse,
    MyDayDetailResponse, MyDayPunch, MyDayBreakSegment,
    BreakAnomalyRow, BreakAnomalySegment, BreakAnomalySummary, BreakAnomalyListResponse,
)
# All shift-policy comparisons happen in IST (the business timezone).
# Storage stays UTC; we only convert when comparing wall-clock times.
# Fixed UTC+5:30 — India doesn't observe DST, so a static offset is correct
# and avoids depending on IANA tzdata being present on Windows.
IST = timezone(timedelta(hours=5, minutes=30))
from app.utils.dependencies import get_current_user, get_current_superuser
from app.utils.hr.attendance_logic import (
    daily_rollup, mark_absentees, lock_day, resolve_shift, verify_geofence, log,
    finalize_orphan_open_punches, GeoVerifyResult, _day_bounds_utc,
)


router = APIRouter(prefix="/hr/attendance", tags=["HR — Attendance"])


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────

def _employee_snapshot(db: Session, employee_id: UUID) -> dict:
    row = (
        db.query(
            Employee.employee_id, User.full_name,
            Designation.name.label("designation_name"),
            Department.name.label("department_name"),
        )
        .join(User, User.id == Employee.user_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .filter(Employee.id == employee_id)
        .first()
    )
    if not row:
        return {}
    return {
        "employee_name": row.full_name,
        "employee_code": row.employee_id,
        "designation": row.designation_name,
        "department": row.department_name,
    }


def _to_att_response(db: Session, a: Attendance) -> AttendanceResponse:
    snap = _employee_snapshot(db, a.employee_id)
    shift = db.query(Shift).filter(Shift.id == a.shift_id).first() if a.shift_id else None
    return AttendanceResponse(
        id=a.id,
        employee_id=a.employee_id,
        date=a.date,
        shift_id=a.shift_id,
        shift_name=shift.name if shift else None,
        check_in_time=a.check_in_time,
        check_out_time=a.check_out_time,
        working_hours=float(a.working_hours or 0),
        break_hours=float(a.break_hours or 0),
        late_minutes=int(a.late_minutes or 0),
        early_exit_minutes=int(a.early_exit_minutes or 0),
        overtime_hours=float(a.overtime_hours or 0),
        status=a.status,
        source=a.source,
        geo_lat=float(a.geo_lat) if a.geo_lat is not None else None,
        geo_lng=float(a.geo_lng) if a.geo_lng is not None else None,
        geo_verified=bool(a.geo_verified),
        device_info=a.device_info,
        remarks=a.remarks,
        is_flagged=bool(a.is_flagged),
        is_locked=bool(a.is_locked),
        late_condoned=bool(getattr(a, "late_condoned", False)),
        lop_days=float(a.lop_days or 0),
        created_at=a.created_at,
        updated_at=a.updated_at,
        **snap,
    )


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


def _resolve_self_employee(db: Session, user: User) -> Employee:
    emp = (
        db.query(Employee)
        .filter(Employee.user_id == user.id, Employee.is_deleted == False)  # noqa: E712
        .first()
    )
    if not emp:
        raise HTTPException(404, "No employee profile linked to your account")
    return emp


# ══════════════════════════════════════════════════════════════════════════
# Admin — Dashboard
# ══════════════════════════════════════════════════════════════════════════

@router.get("/dashboard/stats", response_model=AttendanceDashboardStats)
def dashboard_stats(
    on_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = on_date or date.today()
    headcount = (
        db.query(Employee)
        .filter(Employee.is_deleted == False, Employee.lifecycle_state == LifecycleState.ACTIVE)  # noqa: E712
        .count()
    )
    base = db.query(Attendance).filter(Attendance.date == today, Attendance.is_deleted == False)  # noqa: E712

    present = base.filter(Attendance.status == AttendanceStatus.PRESENT).count()
    late = base.filter(Attendance.status == AttendanceStatus.LATE).count()
    half_day = base.filter(Attendance.status == AttendanceStatus.HALF_DAY).count()
    on_leave = base.filter(Attendance.status == AttendanceStatus.LEAVE).count()
    on_wfh = base.filter(Attendance.status.in_([AttendanceStatus.WFH, AttendanceStatus.REMOTE])).count()
    overtime_count = base.filter(Attendance.overtime_hours > 0).count()
    absent = base.filter(Attendance.status == AttendanceStatus.ABSENT).count()

    total_today = present + late + half_day + absent + on_leave + on_wfh
    on_time_pct = round((present / total_today) * 100, 1) if total_today else 0.0

    pending_corrections = (
        db.query(AttendanceCorrection)
        .filter(AttendanceCorrection.status == CorrectionStatus.PENDING,
                AttendanceCorrection.is_deleted == False)  # noqa: E712
        .count()
    )
    # Late-punch approval requests sit in the corrections table tagged
    # with `[LATE_PUNCH]` in the reason. Until admin approves them, the
    # corresponding IN punch hasn't landed, so they don't show up in
    # `late_count`. Surface them separately so the dashboard reflects
    # work pending admin action.
    pending_late_count = (
        db.query(AttendanceCorrection)
        .filter(
            AttendanceCorrection.status == CorrectionStatus.PENDING,
            AttendanceCorrection.is_deleted == False,  # noqa: E712
            AttendanceCorrection.attendance_date == today,
            AttendanceCorrection.reason.like("[LATE_PUNCH]%"),
        )
        .count()
    )
    pending_wfh = (
        db.query(WfhRequest)
        .filter(WfhRequest.status == WfhStatus.PENDING, WfhRequest.is_deleted == False)  # noqa: E712
        .count()
    )
    biometric_errors = (
        db.query(BiometricDevice)
        .filter(BiometricDevice.last_sync_status == BiometricDeviceStatus.ERROR,
                BiometricDevice.is_deleted == False)  # noqa: E712
        .count()
    )

    return AttendanceDashboardStats(
        headcount=headcount,
        present_today=present + late,  # both walked in
        absent_today=absent,
        on_leave=on_leave,
        on_wfh=on_wfh,
        late_count=late,
        pending_late_count=pending_late_count,
        on_time_pct=on_time_pct,
        overtime_count=overtime_count,
        pending_corrections=pending_corrections,
        pending_wfh=pending_wfh,
        biometric_errors=biometric_errors,
    )


@router.get("/dashboard/by-department", response_model=DashboardByDeptResponse)
def dashboard_by_department(
    on_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = on_date or date.today()
    rows = (
        db.query(Department.name, Attendance.status, func.count(Attendance.id))
        .join(Employee, Employee.department_id == Department.id)
        .join(Attendance, Attendance.employee_id == Employee.id)
        .filter(Attendance.date == today, Attendance.is_deleted == False)  # noqa: E712
        .group_by(Department.name, Attendance.status)
        .all()
    )
    bucket: dict = {}
    for dept, status, c in rows:
        d = bucket.setdefault(dept or "Unassigned", {"present": 0, "absent": 0, "on_leave": 0})
        if status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE, AttendanceStatus.HALF_DAY):
            d["present"] += int(c)
        elif status == AttendanceStatus.ABSENT:
            d["absent"] += int(c)
        elif status == AttendanceStatus.LEAVE:
            d["on_leave"] += int(c)
    items = [DeptAttendance(department=k, **v) for k, v in bucket.items()]
    return DashboardByDeptResponse(items=items)


@router.get("/heatmap", response_model=HeatmapResponse)
def heatmap(
    start: Optional[date] = None,
    end: Optional[date] = None,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """Returns a 7×24 (day × hour) density heatmap for the supplied range.

    Density is computed from the count of distinct (employee, hour) check-ins
    that fall within each (weekday, hour) bucket, normalised against the
    busiest bucket in the range.
    """
    today = date.today()
    end_d = end or today
    start_d = start or (end_d - timedelta(days=6))

    rows = (
        db.query(AttendancePunch)
        .filter(
            AttendancePunch.punch_time >= datetime.combine(start_d, dtime.min, tzinfo=timezone.utc),
            AttendancePunch.punch_time <  datetime.combine(end_d + timedelta(days=1), dtime.min, tzinfo=timezone.utc),
            AttendancePunch.punch_type == PunchType.IN,
        )
        .all()
    )
    grid: dict = {}
    for p in rows:
        wd = p.punch_time.weekday()
        hr = p.punch_time.hour
        grid[(wd, hr)] = grid.get((wd, hr), 0) + 1
    if grid:
        peak = max(grid.values())
    else:
        peak = 1

    cells: List[HeatmapCell] = []
    for wd in range(7):
        for hr in range(24):
            c = grid.get((wd, hr), 0)
            density = round(c / peak, 3) if peak else 0.0
            cells.append(HeatmapCell(day=wd, hour=hr, density=density, present=c))
    return HeatmapResponse(range_start=start_d, range_end=end_d, cells=cells)


# ══════════════════════════════════════════════════════════════════════════
# Admin — Daily list / detail / edit
# ══════════════════════════════════════════════════════════════════════════

@router.get("/today", response_model=AttendanceListResponse)
@router.get("/", response_model=AttendanceListResponse)
def list_attendance(
    date_: Optional[date] = Query(None, alias="date"),
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    shift_id: Optional[UUID] = None,
    status_filter: Optional[AttendanceStatus] = Query(None, alias="status"),
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    target_date = date_ or date.today()

    # When admin views a past date, finalize any orphan IN punches for the
    # employees on that date so the row shows the auto-closed state (real
    # working_hours, populated check_out_time) instead of "Not clocked in"
    # when the admin tries to punch them out.
    if target_date < date.today():
        emp_ids_with_open = [
            r[0] for r in (
                db.query(Attendance.employee_id)
                .filter(
                    Attendance.date == target_date,
                    Attendance.check_in_time.isnot(None),
                    Attendance.check_out_time.is_(None),
                    Attendance.is_deleted == False,  # noqa: E712
                    Attendance.is_locked == False,   # noqa: E712
                )
                .distinct()
                .all()
            )
        ]
        lookback = max(1, (date.today() - target_date).days + 1)
        any_changes = False
        for eid in emp_ids_with_open:
            if finalize_orphan_open_punches(db, eid, lookback_days=lookback, actor_id=None):
                any_changes = True
        if any_changes:
            db.commit()

    query = (
        db.query(Attendance)
        .filter(Attendance.date == target_date, Attendance.is_deleted == False)  # noqa: E712
    )
    if employee_id:
        query = query.filter(Attendance.employee_id == employee_id)
    if shift_id:
        query = query.filter(Attendance.shift_id == shift_id)
    if status_filter:
        query = query.filter(Attendance.status == status_filter)
    if department_id or q:
        query = query.join(Employee, Employee.id == Attendance.employee_id).join(User, User.id == Employee.user_id)
        if department_id:
            query = query.filter(Employee.department_id == department_id)
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(or_(
                func.lower(User.full_name).like(like),
                func.lower(Employee.employee_id).like(like),
            ))

    total = query.count()
    rows = query.order_by(Attendance.check_in_time.asc().nullslast()).offset((page - 1) * limit).limit(limit).all()
    return AttendanceListResponse(
        items=[_to_att_response(db, a) for a in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.get("/{attendance_id}", response_model=AttendanceResponse)
def get_attendance(
    attendance_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(Attendance).filter(Attendance.id == attendance_id, Attendance.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Attendance not found")
    return _to_att_response(db, a)


@router.patch("/{attendance_id}", response_model=AttendanceResponse)
def update_attendance(
    attendance_id: UUID,
    payload: AttendanceUpdate,
    force: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = db.query(Attendance).filter(Attendance.id == attendance_id, Attendance.is_deleted == False).first()  # noqa: E712
    if not a:
        raise HTTPException(404, "Attendance not found")
    if a.is_locked and not force:
        raise HTTPException(409, "Attendance row is locked; pass ?force=true to override (audited)")
    before = {
        "status": a.status.value,
        "check_in_time": a.check_in_time.isoformat() if a.check_in_time else None,
        "check_out_time": a.check_out_time.isoformat() if a.check_out_time else None,
        "remarks": a.remarks,
    }
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(a, k, v)
    a.last_updated_by_id = admin.id
    a.source = AttendanceSource.MANUAL
    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.MANUAL_EDIT,
        target_table="hr_attendance",
        target_id=a.id,
        employee_id=a.employee_id,
        payload={"before": before, "after": data, "forced": force},
    )
    db.commit()
    db.refresh(a)
    return _to_att_response(db, a)


@router.post("/", response_model=AttendanceResponse, status_code=http_status.HTTP_201_CREATED)
def create_attendance(
    payload: AttendanceCreateManual,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    existing = (
        db.query(Attendance)
        .filter(Attendance.employee_id == payload.employee_id, Attendance.date == payload.date)
        .first()
    )
    if existing and not existing.is_deleted:
        raise HTTPException(409, "Attendance for this employee+date already exists; PATCH it instead")
    a = Attendance(
        employee_id=payload.employee_id,
        date=payload.date,
        status=payload.status,
        check_in_time=payload.check_in_time,
        check_out_time=payload.check_out_time,
        remarks=payload.remarks,
        source=AttendanceSource.MANUAL,
        created_by_id=admin.id,
        last_updated_by_id=admin.id,
    )
    db.add(a)
    db.flush()
    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.MANUAL_EDIT,
        target_table="hr_attendance",
        target_id=a.id,
        employee_id=a.employee_id,
        payload={"manual_create": payload.model_dump(mode="json")},
    )
    db.commit()
    db.refresh(a)
    return _to_att_response(db, a)


@router.post("/admin-punch", response_model=PunchResponse, status_code=http_status.HTTP_201_CREATED)
def admin_punch_on_behalf(
    employee_id: UUID,
    payload: PunchCreate,
    on_date: Optional[date] = Query(None, description="Target IST date. Defaults to today. For past dates, OUT punches route through the orphan-finalizer to close the stale row."),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Admin appends a punch for any employee. Bypasses geo-fence but writes audit.

    When `on_date` is in the past and the request is an OUT punch, the endpoint
    routes through `finalize_orphan_open_punches` instead of inserting a punch
    at "now" (which would land in today's window, not the target day's). The
    finalizer injects a synthetic OUT at the last recorded punch time on the
    target date — the same conservative behavior the self-service auto-close
    uses — so the historical day gets closed without crediting time we can't
    prove the employee was actually working.
    """
    emp = db.query(Employee).filter(Employee.id == employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    today = date.today()
    target_date = on_date or today
    requested = payload.punch_type

    # ── Past-date OUT punch: route through the finalizer ──────────────────
    # The legacy path queried today's punches and refused with "Not clocked in"
    # because the IN was on a prior day. The finalizer is the correct primitive
    # for closing forgotten clock-outs after the day rolls over.
    if target_date < today and requested == PunchType.OUT:
        att = (
            db.query(Attendance)
            .filter(
                Attendance.employee_id == emp.id,
                Attendance.date == target_date,
                Attendance.is_deleted == False,  # noqa: E712
            )
            .first()
        )
        if not att:
            raise HTTPException(404, f"No attendance row for {target_date.isoformat()}")
        if att.check_out_time is not None:
            raise HTTPException(409, "Already clocked out for that day")
        if att.check_in_time is None:
            raise HTTPException(409, "No clock-in on that day to close")
        # Process all orphan open IN punches in the lookback window (covers cases
        # where the employee left multiple open days). Finalizer is idempotent.
        lookback = max(1, (today - target_date).days + 1)
        finalized = finalize_orphan_open_punches(db, emp.id, lookback_days=lookback, actor_id=admin.id)
        if finalized == 0:
            raise HTTPException(409, "No open punches to finalize — clock-in row had no punch evidence")
        db.commit()
        # Return the synthetic OUT punch we just created for that day.
        day_start_utc, day_end_utc = _day_bounds_utc(target_date)
        latest_out = (
            db.query(AttendancePunch)
            .filter(
                AttendancePunch.employee_id == emp.id,
                AttendancePunch.punch_type == PunchType.OUT,
                AttendancePunch.punch_time >= day_start_utc,
                AttendancePunch.punch_time <  day_end_utc,
            )
            .order_by(AttendancePunch.punch_time.desc())
            .first()
        )
        if not latest_out:
            raise HTTPException(500, "Finalizer reported success but no OUT punch was found")
        return PunchResponse.model_validate(latest_out)

    # ── Today (existing path) ─────────────────────────────────────────────
    # IST-bounded day window (punches are stored UTC; the business clock is IST).
    today_start_utc_admin = datetime.combine(target_date, dtime.min, tzinfo=IST).astimezone(timezone.utc)
    tomorrow_start_utc_admin = datetime.combine(target_date + timedelta(days=1), dtime.min, tzinfo=IST).astimezone(timezone.utc)
    punches = (
        db.query(AttendancePunch)
        .filter(
            AttendancePunch.employee_id == emp.id,
            AttendancePunch.punch_time >= today_start_utc_admin,
            AttendancePunch.punch_time <  tomorrow_start_utc_admin,
        )
        .order_by(AttendancePunch.punch_time.asc())
        .all()
    )
    has_open_in = False
    has_close_out = False
    for p in punches:
        if p.punch_type == PunchType.IN:
            has_open_in = True
        elif p.punch_type == PunchType.OUT:
            has_close_out = True
            has_open_in = False
    if requested == PunchType.IN and has_open_in:
        raise HTTPException(409, "Already clocked in")
    if requested == PunchType.OUT and not has_open_in:
        raise HTTPException(409, "Not clocked in")
    if has_close_out and requested == PunchType.IN:
        raise HTTPException(409, "Already clocked out for today")
    punch = AttendancePunch(
        employee_id=emp.id,
        punch_time=datetime.now(timezone.utc),
        punch_type=requested,
        source=AttendanceSource.MANUAL,
        geo_lat=payload.geo_lat,
        geo_lng=payload.geo_lng,
        geo_verified=True,
        payload={"admin_override": True, "by": str(admin.id)},
    )
    db.add(punch)
    db.flush()
    daily_rollup(db, emp.id, target_date, actor_id=admin.id, source=AttendanceSource.MANUAL)
    log(
        db, actor_id=admin.id, action=AttendanceLogAction.MANUAL_EDIT,
        target_table="hr_attendance_punches", target_id=punch.id, employee_id=emp.id,
        payload={"type": requested.value, "via": "admin-punch", "on_date": target_date.isoformat()},
    )
    db.commit()
    db.refresh(punch)
    return PunchResponse.model_validate(punch)


@router.post("/recompute", response_model=AttendanceResponse)
def recompute_attendance(
    employee_id: UUID,
    on_date: date,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    a = daily_rollup(db, employee_id, on_date, actor_id=admin.id, source=AttendanceSource.SYSTEM)
    db.commit()
    db.refresh(a)
    return _to_att_response(db, a)


@router.post("/condone-late", response_model=AttendanceResponse)
def condone_late(
    employee_id: UUID,
    on_date: date,
    condoned: bool = True,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Waive (or un-waive) a LATE mark so it no longer counts toward the monthly
    late-accumulation penalty — corporate "regularisation". The punch and the
    LATE status stay on record; only the penalty is reconciled. Recompute then
    releases (or re-applies) the LWP late penalty for that month.
    """
    a = (
        db.query(Attendance)
        .filter(Attendance.employee_id == employee_id, Attendance.date == on_date,
                Attendance.is_deleted == False)  # noqa: E712
        .first()
    )
    if not a:
        raise HTTPException(404, "Attendance row not found for that employee/date")
    if a.status != AttendanceStatus.LATE:
        raise HTTPException(409, f"Only LATE days can be condoned (this is {a.status.value})")
    a.late_condoned = bool(condoned)
    db.flush()
    log(
        db, actor_id=admin.id,
        action=AttendanceLogAction.MANUAL_EDIT,
        target_table="hr_attendance", target_id=a.id, employee_id=employee_id,
        payload={"on_date": on_date.isoformat(), "late_condoned": bool(condoned),
                 "reason": "late_mark_regularisation"},
    )
    # Reconcile the month's penalty with the new condone state.
    daily_rollup(db, employee_id, on_date, actor_id=admin.id, source=AttendanceSource.MANUAL)
    db.commit()
    db.refresh(a)
    return _to_att_response(db, a)


# ══════════════════════════════════════════════════════════════════════════
# Admin — Break-anomaly monitoring
# ──────────────────────────────────────────────────────────────────────────
# Excess break time was previously only enforced at break-start (a 409
# response). After the fact, admins had no surface that said "who took 3 hours
# of breaks today vs the 60-minute cap". This endpoint surfaces every row
# where break_actual_minutes > shift.break_minutes, with severity buckets
# (mild / severe / critical) driving the UI's color coding.
#
# NOTE: This route is DECLARED BELOW but ROUTED AT TOP via add_api_route()
# right after /today. See the `# Route order patch` block above /{attendance_id}.
# ══════════════════════════════════════════════════════════════════════════

@router.get("/break-anomalies", response_model=BreakAnomalyListResponse)
def break_anomalies(
    on_date: Optional[date] = Query(None, description="IST calendar date. Defaults to today."),
    department_id: Optional[UUID] = None,
    severity: Optional[str] = Query(None, pattern="^(MILD|SEVERE|CRITICAL|WITHIN_CAP)$"),
    q: Optional[str] = Query(None, description="Substring search on employee name or code"),
    include_within_cap: bool = Query(False, description="Also surface employees whose total break time is within their shift's cap. Useful for the detailed Excess-Breaks page where admins want to see *every* employee with break activity, not only those who breached policy."),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    """List employees whose total break time on a given date exceeded the
    shift's `break_minutes` cap. Severity buckets:
      • MILD     — 1.0× to 1.5× of cap
      • SEVERE   — 1.5× to 2.0× of cap
      • CRITICAL — > 2.0× of cap (or > cap + 60 minutes, whichever is bigger)
    Each row carries the full break-segment tape so the UI can highlight
    individual breaks that ran long.
    """
    target_date = on_date or date.today()
    day_start_utc, day_end_utc = _day_bounds_utc(target_date)

    base = (
        db.query(Attendance)
        .filter(
            Attendance.date == target_date,
            Attendance.is_deleted == False,  # noqa: E712
            Attendance.break_hours > 0,
        )
    )
    if department_id or q:
        base = (
            base.join(Employee, Employee.id == Attendance.employee_id)
            .join(User, User.id == Employee.user_id)
        )
        if department_id:
            base = base.filter(Employee.department_id == department_id)
        if q:
            like = f"%{q.lower()}%"
            base = base.filter(or_(
                func.lower(User.full_name).like(like),
                func.lower(Employee.employee_id).like(like),
            ))

    candidate_rows = base.all()

    def _severity_for(ratio: float, excess_min: int) -> str:
        if ratio > 2.0 or excess_min > 60:
            return "CRITICAL"
        if ratio > 1.5:
            return "SEVERE"
        return "MILD"

    flagged_rows = []
    for att in candidate_rows:
        shift = resolve_shift(db, att.employee_id, att.date)
        cap_min = int((shift.break_minutes if shift else 60) or 60)
        if cap_min <= 0:
            continue
        actual_min = int(round(float(att.break_hours or 0) * 60))
        is_within_cap = actual_min <= cap_min
        if is_within_cap and not include_within_cap:
            continue  # within cap — not surfaced unless the caller asked
        if is_within_cap:
            # Within-cap rows still need a row payload so the detailed page
            # can render them. They get a neutral severity tag and zero excess.
            excess_min = 0
            ratio = actual_min / cap_min if cap_min else 0.0
            sev = "WITHIN_CAP"
        else:
            excess_min = actual_min - cap_min
            ratio = actual_min / cap_min
            sev = _severity_for(ratio, excess_min)
        if severity and sev != severity:
            continue

        # Pull break segments from raw punches for the day.
        punches = (
            db.query(AttendancePunch)
            .filter(
                AttendancePunch.employee_id == att.employee_id,
                AttendancePunch.punch_time >= day_start_utc,
                AttendancePunch.punch_time <  day_end_utc,
            )
            .order_by(AttendancePunch.punch_time.asc())
            .all()
        )
        segments: list = []
        open_start = None
        windows = (shift.break_windows if shift else None) or []
        def _in_window(p_time):
            if not windows:
                return True
            local = p_time.astimezone(IST).time()
            for w in windows:
                try:
                    ws = w.get("start_time"); we = w.get("end_time")
                    if ws and we:
                        ws_t = dtime.fromisoformat(ws) if isinstance(ws, str) else ws
                        we_t = dtime.fromisoformat(we) if isinstance(we, str) else we
                        if ws_t <= local <= we_t:
                            return True
                except Exception:
                    continue
            return False

        for p in punches:
            if p.punch_type == PunchType.BREAK_START:
                open_start = p
            elif p.punch_type == PunchType.BREAK_END and open_start is not None:
                dur_min = (p.punch_time - open_start.punch_time).total_seconds() / 60.0
                segments.append(BreakAnomalySegment(
                    start=open_start.punch_time, end=p.punch_time,
                    minutes=round(max(0.0, dur_min), 1), is_open=False,
                    is_over_window=not _in_window(open_start.punch_time),
                ))
                open_start = None
        if open_start is not None:
            segments.append(BreakAnomalySegment(
                start=open_start.punch_time, end=None, minutes=0.0, is_open=True,
                is_over_window=not _in_window(open_start.punch_time),
            ))

        emp = att.employee
        emp_user = getattr(emp, "user", None) if emp else None
        dept = getattr(emp, "department", None) if emp else None

        flagged_rows.append(BreakAnomalyRow(
            employee_id=att.employee_id,
            employee_name=getattr(emp_user, "full_name", None) if emp_user else None,
            employee_code=getattr(emp, "employee_id", None) if emp else None,
            department_name=getattr(dept, "name", None) if dept else None,
            date=att.date,
            shift_name=getattr(shift, "name", None) if shift else None,
            break_cap_minutes=cap_min,
            break_actual_minutes=actual_min,
            excess_minutes=excess_min,
            overage_ratio=round(ratio, 3),
            severity=sev,
            break_count=len(segments),
            segments=segments,
            status=att.status,
            is_flagged=bool(att.is_flagged),
            has_open_break=any(s.is_open for s in segments),
        ))

    # Sort: critical first, then by excess minutes descending. Within-cap rows
    # sort below all flagged severities, ordered by break duration descending
    # so the longest near-policy break is the easiest to spot.
    severity_rank = {"CRITICAL": 0, "SEVERE": 1, "MILD": 2, "WITHIN_CAP": 3}
    flagged_rows.sort(key=lambda r: (
        severity_rank.get(r.severity, 9),
        -(r.excess_minutes if r.severity != "WITHIN_CAP" else r.break_actual_minutes),
    ))

    # Summary stats (computed BEFORE pagination so the strip totals match the
    # whole queue, not just the current page). `total_flagged` counts only
    # over-cap rows so the headline number doesn't include WITHIN_CAP entries
    # surfaced for transparency.
    over_cap_rows = [r for r in flagged_rows if r.severity != "WITHIN_CAP"]
    summary = BreakAnomalySummary(
        on_date=target_date,
        total_flagged=len(over_cap_rows),
        mild_count=sum(1 for r in flagged_rows if r.severity == "MILD"),
        severe_count=sum(1 for r in flagged_rows if r.severity == "SEVERE"),
        critical_count=sum(1 for r in flagged_rows if r.severity == "CRITICAL"),
        within_cap_count=sum(1 for r in flagged_rows if r.severity == "WITHIN_CAP"),
        open_break_count=sum(1 for r in flagged_rows if r.has_open_break),
        total_excess_minutes=sum(r.excess_minutes for r in over_cap_rows),
        avg_overage_ratio=round(
            sum(r.overage_ratio for r in over_cap_rows) / len(over_cap_rows), 3
        ) if over_cap_rows else 0.0,
    )

    total = len(flagged_rows)
    paged = flagged_rows[(page - 1) * limit:(page - 1) * limit + limit]
    return BreakAnomalyListResponse(
        summary=summary, items=paged,
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit and total else 1,
    )


# ──────────────────────────────────────────────────────────────────────────
# Route-order patch (FastAPI matches routes in DECLARATION order)
# ──────────────────────────────────────────────────────────────────────────
# `/break-anomalies` is declared below `/{attendance_id}` for readability, but
# Starlette would otherwise match the literal string "break-anomalies" against
# `/{attendance_id}` first and fail UUID parsing with a 422. Lift the route to
# the front of `router.routes` so it wins.
def _hoist_route(path: str) -> None:
    for i, r in enumerate(router.routes):
        if getattr(r, "path", None) == path:
            router.routes.insert(0, router.routes.pop(i))
            return
_hoist_route("/hr/attendance/break-anomalies")


@router.post("/mark-absentees")
def trigger_mark_absentees(
    on_date: Optional[date] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    target = on_date or date.today()
    n = mark_absentees(db, target, actor_id=admin.id)
    db.commit()
    return {"date": target.isoformat(), "marked": n}


@router.post("/lock-day")
def trigger_lock_day(
    on_date: Optional[date] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    target = on_date or date.today() - timedelta(days=1)
    n = lock_day(db, target, actor_id=admin.id)
    db.commit()
    return {"date": target.isoformat(), "locked": n}


# ══════════════════════════════════════════════════════════════════════════
# User self-service /me block
# ══════════════════════════════════════════════════════════════════════════

@router.get("/me/today", response_model=MeTodayResponse)
def me_today(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    emp = _resolve_self_employee(db, user)
    today = date.today()

    # Self-heal: if the user forgot to clock out yesterday (or earlier within
    # the lookback window) and the day rolled over, finalize the orphan IN now
    # so today's view shows accurate hours / status for that prior day. Safe
    # to run on every page load — idempotent.
    if finalize_orphan_open_punches(db, emp.id, lookback_days=7, actor_id=None):
        db.commit()

    shift = resolve_shift(db, emp.id, today)

    a = (
        db.query(Attendance)
        .filter(Attendance.employee_id == emp.id, Attendance.date == today,
                Attendance.is_deleted == False)  # noqa: E712
        .first()
    )

    # Determine open punch. Query bounds are in IST (business day) — punches
    # are stored UTC; we convert the IST day window to UTC for the filter.
    today_start_utc = datetime.combine(today, dtime.min, tzinfo=IST).astimezone(timezone.utc)
    tomorrow_start_utc = datetime.combine(today + timedelta(days=1), dtime.min, tzinfo=IST).astimezone(timezone.utc)
    punches = (
        db.query(AttendancePunch)
        .filter(AttendancePunch.employee_id == emp.id,
                AttendancePunch.punch_time >= today_start_utc,
                AttendancePunch.punch_time <  tomorrow_start_utc)
        .order_by(AttendancePunch.punch_time.asc())
        .all()
    )
    open_punch = None
    has_open_in = False
    has_close_out = False
    on_break = False
    for p in punches:
        if p.punch_type == PunchType.IN:
            has_open_in = True
            on_break = False
        elif p.punch_type == PunchType.OUT:
            has_close_out = True
            has_open_in = False
        elif p.punch_type == PunchType.BREAK_START:
            on_break = True
        elif p.punch_type == PunchType.BREAK_END:
            on_break = False
    if on_break:
        open_punch = PunchType.BREAK_START
    elif has_open_in and not has_close_out:
        open_punch = PunchType.IN

    elapsed_seconds = 0
    if a and a.check_in_time:
        end = a.check_out_time or datetime.now(timezone.utc)
        elapsed_seconds = int(max(0, (end - a.check_in_time).total_seconds()))

    # Holiday today?
    is_holiday = False
    holiday_name: Optional[str] = None
    # RESTRICTED holidays don't auto-mark today as a holiday — they're
    # optional days employees must explicitly claim.
    from app.models.hr.holiday import HolidayType
    h = (
        db.query(Holiday)
        .filter(
            Holiday.date == today,
            Holiday.is_active == True,            # noqa: E712
            Holiday.is_deleted == False,          # noqa: E712
            Holiday.holiday_type != HolidayType.RESTRICTED,
        )
        .first()
    )
    if h and (h.location_id is None or emp.work_location_id == h.location_id):
        is_holiday = True
        holiday_name = h.name

    # Week off?
    is_week_off = bool(shift and today.weekday() in (shift.weekly_off_days or []))

    # Full-day approved leave? (half-day leave does NOT block — works the other half)
    leave_today = _approved_full_day_leave(db, emp.id, today)
    is_on_leave = leave_today is not None
    leave_type_val = (
        leave_today.leave_type.value if (is_on_leave and hasattr(leave_today.leave_type, "value"))
        else (str(leave_today.leave_type) if is_on_leave else None)
    )
    leave_reference_no = leave_today.reference_no if is_on_leave else None

    # WFH approved?
    wfh = (
        db.query(WfhRequest)
        .filter(
            WfhRequest.employee_id == emp.id,
            WfhRequest.status == WfhStatus.APPROVED,
            WfhRequest.wfh_date <= today,
            WfhRequest.is_deleted == False,  # noqa: E712
        )
        .all()
    )
    wfh_matches = [w for w in wfh if (w.wfh_date_until or w.wfh_date) >= today]
    wfh_approved = bool(wfh_matches)
    # Surface the discriminator so the live chip reads "Remote approved" vs
    # "WFH approved" instead of always saying WFH. REMOTE wins if any covering
    # request is REMOTE (rare to have both for one day).
    wfh_request_type = None
    if wfh_matches:
        wfh_request_type = (
            WfhRequestType.REMOTE.value
            if any(w.request_type == WfhRequestType.REMOTE for w in wfh_matches)
            else WfhRequestType.WFH.value
        )

    can_clock_in = open_punch is None and not has_close_out
    # A weekly-off or company holiday is a non-working day — no self clock-in
    # (matches the /me/clock-in enforcement). An approved WFH/remote waives it.
    if (is_week_off or is_holiday) and not wfh_approved:
        can_clock_in = False
    # Full-day approved leave blocks self clock-in outright (not WFH-waivable).
    if is_on_leave:
        can_clock_in = False
    can_clock_out = open_punch == PunchType.IN
    can_break_start = open_punch == PunchType.IN
    can_break_end = open_punch == PunchType.BREAK_START

    # Policy state — drives the user-page UX
    now_local = _now_local()
    is_late = False
    minutes_late = 0
    requires_late_approval = False
    late_window_closed = False
    late_punch_cutoff_min = 0
    is_too_early_to_punch = False
    minutes_until_clock_in_opens = 0
    minutes_until_shift_start = 0
    clock_in_opens_at_str: Optional[str] = None
    requires_early_exit_approval = False
    minutes_until_shift_end = 0
    has_approved_early_exit = False

    # Half-day state — read once, reused for every windowed check below so
    # FIRST-off employees aren't 4+ hours "late" at midday and SECOND-off
    # employees can leave at mid-shift without triggering the early-exit
    # approval flow.
    half_day = _approved_half_day(db, emp.id, today) if shift else None
    effective_start_str: Optional[str] = None
    effective_end_str: Optional[str] = None
    effective_grace = 0

    if shift:
        effective_start_dt, effective_end_dt = _effective_shift_window(shift, today, half_day)
        effective_start_str = effective_start_dt.strftime("%H:%M")
        effective_end_str = effective_end_dt.strftime("%H:%M")
        minutes_until_shift_start = max(0, int((effective_start_dt - now_local).total_seconds() // 60))
        minutes_until_shift_end = max(0, int((effective_end_dt - now_local).total_seconds() // 60))

        effective_grace = _effective_grace_minutes(shift, half_day)
        minutes_late = _minutes_late_now(shift, now_local, effective_start=effective_start_dt)
        is_late = minutes_late > effective_grace
        threshold = int(shift.late_self_punch_threshold_minutes or 0)

        # Early clock-in lock: anything more than EARLY_PUNCH_BUFFER_MINUTES
        # before the *effective* shift start is rejected on submit. Surface a
        # friendly state so the clock-in button renders disabled with a
        # "shift hasn't started yet" caption.
        if minutes_until_shift_start > EARLY_PUNCH_BUFFER_MINUTES and can_clock_in:
            is_too_early_to_punch = True
            can_clock_in = False
            minutes_until_clock_in_opens = minutes_until_shift_start - EARLY_PUNCH_BUFFER_MINUTES
            clock_in_opens_at_dt = effective_start_dt - timedelta(minutes=EARLY_PUNCH_BUFFER_MINUTES)
            clock_in_opens_at_str = clock_in_opens_at_dt.strftime("%H:%M")

        # Cap the self-late-punch window. Beyond the half-day-late cutoff — the
        # EARLIER of mid-shift or 2h after the effective start — OR once the
        # effective shift end has passed, a self late-punch no longer makes
        # sense: the day is a half-day/no-show that must go through admin
        # regularization, not the quick late-punch button. This mirrors the
        # `min(shift_span/2, 120)` half-day cutoff used by the daily rollup.
        _eff_span_min = (effective_end_dt - effective_start_dt).total_seconds() / 60.0
        if _eff_span_min <= 0:                       # overnight effective window
            _eff_span_min += 24 * 60
        late_punch_cutoff_min = int(min(_eff_span_min / 2.0, 120.0))
        shift_has_ended = now_local >= effective_end_dt

        if can_clock_in and shift.late_punch_requires_approval and minutes_late > effective_grace + threshold:
            if minutes_late <= late_punch_cutoff_min and not shift_has_ended:
                requires_late_approval = True
                can_clock_in = False  # frontend surfaces "Request late approval" instead
            else:
                # Past the cap — lock self clock-in but do NOT offer the
                # late-punch request; the frontend shows a terminal
                # "window closed — contact HR" state instead.
                can_clock_in = False
                late_window_closed = True

        # Early clock-out: if the *effective* shift end hasn't passed and
        # there's no approved early-exit correction, route the Clock-out
        # button into the request modal. A SECOND-off employee at 13:31
        # (mid-shift effective end) is allowed to leave cleanly.
        if open_punch == PunchType.IN and now_local < effective_end_dt:
            has_approved_early_exit = _has_approved_correction(db, emp.id, today, "[EARLY_EXIT]")
            if not has_approved_early_exit:
                requires_early_exit_approval = True

    pending_late_request_id = None
    pending_late_request_status = None
    pending_early_exit_request_id = None
    pending_early_exit_request_status = None
    # Look up the latest correction for today to surface its status.
    latest_corrs = (
        db.query(AttendanceCorrection)
        .filter(
            AttendanceCorrection.employee_id == emp.id,
            AttendanceCorrection.attendance_date == today,
        )
        .order_by(AttendanceCorrection.created_at.desc())
        .all()
    )
    for corr in latest_corrs:
        reason = corr.reason or ""
        if reason.startswith("[LATE_PUNCH]") and pending_late_request_id is None:
            if corr.status in (CorrectionStatus.PENDING, CorrectionStatus.APPROVED):
                pending_late_request_id = corr.id
                pending_late_request_status = corr.status.value
        elif reason.startswith("[EARLY_EXIT]") and pending_early_exit_request_id is None:
            pending_early_exit_request_id = corr.id
            pending_early_exit_request_status = corr.status.value

    break_used = _break_used_minutes_today(punches, now_local) if shift else 0
    break_cap = int(shift.break_minutes or 0) if shift else 0
    current_window, next_window, in_window_now = _classify_break_windows(shift, now_local) if shift else (None, None, True)

    if open_punch == PunchType.BREAK_START:
        next_action = "break_end"
    elif open_punch == PunchType.IN:
        next_action = "request_early_exit" if requires_early_exit_approval else "clock_out"
    elif has_close_out:
        next_action = "done"
    elif requires_late_approval:
        next_action = "request_late_approval"
    elif late_window_closed:
        next_action = "late_window_closed"
    elif is_too_early_to_punch:
        next_action = "wait_for_shift"
    else:
        next_action = "clock_in"

    return MeTodayResponse(
        employee_id=emp.id,
        today=today,
        shift=_to_shift_response(shift) if shift else None,
        attendance=_to_att_response(db, a) if a else None,
        open_punch=open_punch,
        elapsed_seconds=elapsed_seconds,
        can_clock_in=can_clock_in,
        can_clock_out=can_clock_out,
        can_break_start=can_break_start,
        can_break_end=can_break_end,
        is_holiday=is_holiday,
        holiday_name=holiday_name,
        is_week_off=is_week_off,
        wfh_approved=wfh_approved,
        wfh_request_type=wfh_request_type,
        is_on_leave=is_on_leave,
        leave_type=leave_type_val,
        leave_reference_no=leave_reference_no,
        next_action=next_action,
        is_late=is_late,
        late_minutes_now=max(0, minutes_late),
        requires_late_approval=requires_late_approval,
        late_window_closed=late_window_closed,
        late_punch_cutoff_minutes=late_punch_cutoff_min,
        pending_late_request_id=pending_late_request_id,
        pending_late_request_status=pending_late_request_status,
        is_too_early_to_punch=is_too_early_to_punch,
        minutes_until_clock_in_opens=minutes_until_clock_in_opens,
        clock_in_opens_at=clock_in_opens_at_str,
        minutes_until_shift_start=minutes_until_shift_start,
        requires_early_exit_approval=requires_early_exit_approval,
        minutes_until_shift_end=minutes_until_shift_end,
        pending_early_exit_request_id=pending_early_exit_request_id,
        pending_early_exit_request_status=pending_early_exit_request_status,
        has_approved_early_exit=has_approved_early_exit,
        break_used_minutes=break_used,
        break_remaining_minutes=max(0, break_cap - break_used),
        current_break_window=current_window,
        next_break_window=next_window,
        in_break_window_now=in_window_now,
        is_half_day=half_day is not None,
        half_day_which=half_day.which_half.value if half_day else None,
        half_day_reason=(half_day.reason if half_day else None),
        effective_shift_start=effective_start_str,
        effective_shift_end=effective_end_str,
        effective_grace_minutes=effective_grace,
    )


# ──────────────────────────────────────────────────────────────────────────
# Punch-policy helpers (late punch + break windows)
# ──────────────────────────────────────────────────────────────────────────

def _hhmm_to_time(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _now_local() -> datetime:
    """Current wall-clock time in IST. Storage uses UTC; comparisons use IST."""
    return datetime.now(IST)


def _shift_start_local(shift: Shift, on_date: date) -> datetime:
    return datetime.combine(on_date, shift.start_time, tzinfo=IST)


def _approved_half_day(
    db: Session, employee_id: UUID, on_date: date,
) -> Optional[HalfDayRequest]:
    """Return the APPROVED half-day request for (employee, date) or None.

    Used by every late/early/grace check so a first-half-off employee isn't
    treated as 4+ hours late at midday, and a second-half-off employee can
    clock out at mid-shift without tripping early-exit approval.
    """
    return (
        db.query(HalfDayRequest)
        .filter(
            HalfDayRequest.employee_id == employee_id,
            HalfDayRequest.half_day_date == on_date,
            HalfDayRequest.status == HalfDayStatus.APPROVED,
            HalfDayRequest.is_deleted == False,  # noqa: E712
        )
        .first()
    )


def _effective_shift_window(
    shift: Shift, on_date: date, half_day: Optional[HalfDayRequest],
) -> tuple[datetime, datetime]:
    """Return (effective_start, effective_end) wall-clock IST datetimes.

    For a half-day:
      * FIRST off  → effective_start = midpoint(start, end), end unchanged
      * SECOND off → start unchanged, effective_end = midpoint(start, end)

    Midpoint uses the raw shift span so it doesn't depend on break_minutes.
    Wraps past midnight when start > end (night shift) — same convention as
    `_shift_end_local`.
    """
    start_dt = datetime.combine(on_date, shift.start_time, tzinfo=IST)
    end_dt = datetime.combine(on_date, shift.end_time, tzinfo=IST)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    if not half_day:
        return start_dt, end_dt

    mid_dt = start_dt + (end_dt - start_dt) / 2
    if half_day.which_half == HalfDayWhich.FIRST:
        return mid_dt, end_dt
    return start_dt, mid_dt


def _effective_grace_minutes(shift: Shift, half_day: Optional[HalfDayRequest]) -> int:
    """`half_day_grace_minutes` if a half-day is active today, else `grace_minutes`."""
    if half_day:
        return int(getattr(shift, "half_day_grace_minutes", None) or shift.grace_minutes or 0)
    return int(shift.grace_minutes or 0)


def _minutes_late_now(
    shift: Shift, now_local: datetime,
    *, effective_start: Optional[datetime] = None,
) -> int:
    """Minutes past the effective shift start (negative if before).

    Callers that have already resolved the effective start should pass it in;
    otherwise this falls back to `shift.start_time` (legacy behaviour).
    """
    start = effective_start or _shift_start_local(shift, now_local.date())
    return int((now_local - start).total_seconds() // 60)


def _break_used_minutes_today(today_punches: list, now_local: datetime) -> int:
    """Sum of completed break durations + currently open break duration."""
    total = 0
    open_start_local: Optional[datetime] = None
    for p in today_punches:
        # punch_time is stored UTC-aware → convert to IST for elapsed math.
        pt = p.punch_time
        if pt.tzinfo is None:
            pt = pt.replace(tzinfo=timezone.utc)
        pt_local = pt.astimezone(IST)
        if p.punch_type == PunchType.BREAK_START:
            open_start_local = pt_local
        elif p.punch_type == PunchType.BREAK_END and open_start_local is not None:
            total += int(max(0, (pt_local - open_start_local).total_seconds() // 60))
            open_start_local = None
    if open_start_local is not None:
        total += int(max(0, (now_local - open_start_local).total_seconds() // 60))
    return total


def _classify_break_windows(shift: Shift, now_local: datetime):
    """Return (current_window, next_window, in_window_now) where each window
    is a CurrentBreakWindow dict-shape or None."""
    windows = list(shift.break_windows or [])
    if not windows:
        return None, None, True
    current_t = now_local.time()
    current = None
    next_w = None
    for w in windows:
        try:
            st = _hhmm_to_time(w["start_time"])
            et = _hhmm_to_time(w["end_time"])
        except Exception:
            continue
        if st <= current_t <= et:
            current = CurrentBreakWindow(
                label=w["label"], start_time=w["start_time"], end_time=w["end_time"],
                max_minutes=int(w["max_minutes"]), is_active_now=True,
                minutes_until_end=max(0, int((datetime.combine(now_local.date(), et, tzinfo=IST) - now_local).total_seconds() // 60)),
            )
            break
    if current is None:
        future = sorted(
            [w for w in windows if _hhmm_to_time(w["start_time"]) > current_t],
            key=lambda w: w["start_time"],
        )
        if future:
            w = future[0]
            st = _hhmm_to_time(w["start_time"])
            next_w = CurrentBreakWindow(
                label=w["label"], start_time=w["start_time"], end_time=w["end_time"],
                max_minutes=int(w["max_minutes"]), is_active_now=False,
                minutes_until_start=max(0, int((datetime.combine(now_local.date(), st, tzinfo=IST) - now_local).total_seconds() // 60)),
            )
    return current, next_w, current is not None


# How early (in minutes) the user is allowed to clock in before shift start.
# Punching earlier than this requires admin pre-approval — the late-punch
# approval correction flow is reused with a different reason tag.
EARLY_PUNCH_BUFFER_MINUTES = 30


def _shift_end_local(shift: Shift, on_date: date) -> datetime:
    """Wall-clock shift end in IST. If end_time is earlier than start_time the
    shift wraps past midnight (e.g. 22:00–06:00) — push end to the next day.
    """
    start = datetime.combine(on_date, shift.start_time, tzinfo=IST)
    end = datetime.combine(on_date, shift.end_time, tzinfo=IST)
    if end <= start:
        end += timedelta(days=1)
    return end


def _has_approved_correction(
    db: Session, employee_id: UUID, on_date: date, tag: str,
) -> bool:
    """Whether an admin-approved correction with the given reason-tag exists
    for the employee on the date. The early-exit and late-punch flows use
    `[EARLY_EXIT]` / `[LATE_PUNCH]` tags in the reason string.
    """
    row = (
        db.query(AttendanceCorrection.id)
        .filter(
            AttendanceCorrection.employee_id == employee_id,
            AttendanceCorrection.attendance_date == on_date,
            AttendanceCorrection.status == CorrectionStatus.APPROVED,
            AttendanceCorrection.reason.like(f"{tag}%"),
        )
        .first()
    )
    return row is not None


def _validate_punch_policy(
    shift: Optional[Shift],
    expected_type: PunchType,
    today_punches: list,
    *,
    db: Optional[Session] = None,
    employee_id: Optional[UUID] = None,
):
    """Raise HTTPException if policy rules are violated.

    Now covers four cases:
      - clock-in too early (more than `EARLY_PUNCH_BUFFER_MINUTES` before shift)
      - clock-in too late (past grace + threshold) — requires admin approval
      - clock-out before shift end — requires admin approval (early-exit)
      - break outside window or past daily cap

    For clock-out the function may need to query the DB to check whether an
    approved early-exit correction exists, so callers pass `db` + `employee_id`.
    """
    if not shift:
        return
    now_local = _now_local()
    today = now_local.date()

    # Resolve half-day awareness so a FIRST-off employee at 14:00 isn't treated
    # as ~5h late, and a SECOND-off employee can clock in normally for the
    # morning half. Requires db + employee_id — callers always pass them on
    # IN/OUT today; legacy callers without them fall back to nominal shift.
    half_day = (
        _approved_half_day(db, employee_id, today)
        if db is not None and employee_id is not None
        else None
    )
    effective_start, effective_end = _effective_shift_window(shift, today, half_day)
    grace = _effective_grace_minutes(shift, half_day)
    effective_start_str = effective_start.strftime("%H:%M")

    if expected_type == PunchType.IN:
        # Block punches that arrive far before the *effective* shift starts.
        minutes_before_start = int((effective_start - now_local).total_seconds() // 60)
        if minutes_before_start > EARLY_PUNCH_BUFFER_MINUTES:
            raise HTTPException(
                status_code=423,
                detail={
                    "code": "EARLY_PUNCH_NOT_ALLOWED",
                    "message": (
                        f"Your {'half-day ' if half_day else ''}shift starts at {effective_start_str}. "
                        f"You're {minutes_before_start} minutes early — clock-in opens "
                        f"{EARLY_PUNCH_BUFFER_MINUTES} minutes before shift start."
                    ),
                    "shift_start": effective_start_str,
                    "minutes_before_start": minutes_before_start,
                    "early_buffer_minutes": EARLY_PUNCH_BUFFER_MINUTES,
                    "clock_in_opens_at": (effective_start - timedelta(minutes=EARLY_PUNCH_BUFFER_MINUTES)).strftime("%H:%M"),
                    "is_half_day": half_day is not None,
                    "half_day_which": half_day.which_half.value if half_day else None,
                },
            )

        if shift.late_punch_requires_approval:
            threshold = int(shift.late_self_punch_threshold_minutes or 0)
            minutes_late = _minutes_late_now(shift, now_local, effective_start=effective_start)
            if minutes_late > grace + threshold:
                raise HTTPException(
                    status_code=423,
                    detail={
                        "code": "LATE_PUNCH_REQUIRES_APPROVAL",
                        "message": (
                            f"You are {minutes_late} minutes late "
                            f"({'half-day · ' if half_day else ''}start {effective_start_str}). "
                            f"Self-punch is locked beyond {grace + threshold} min "
                            f"({grace} grace + {threshold} threshold). "
                            "Submit a late-punch request for admin approval."
                        ),
                        "shift_start": effective_start_str,
                        "minutes_late": minutes_late,
                        "threshold_minutes": grace + threshold,
                        "is_half_day": half_day is not None,
                        "half_day_which": half_day.which_half.value if half_day else None,
                    },
                )
        return

    if expected_type == PunchType.OUT:
        # Block early clock-out unless admin has approved an early-exit request
        # for today. Reuses the AttendanceCorrection table tagged with
        # `[EARLY_EXIT]` (see /me/request-early-exit). When SECOND-half-off is
        # approved, the effective end is mid-shift — leaving any time after
        # that is fine and shouldn't trigger the early-exit flow.
        effective_end_str = effective_end.strftime("%H:%M")
        if now_local < effective_end:
            approved = False
            if db is not None and employee_id is not None:
                approved = _has_approved_correction(db, employee_id, today, "[EARLY_EXIT]")
            if not approved:
                minutes_remaining = int((effective_end - now_local).total_seconds() // 60)
                raise HTTPException(
                    status_code=423,
                    detail={
                        "code": "EARLY_EXIT_REQUIRES_APPROVAL",
                        "message": (
                            f"Your {'half-day ' if half_day else ''}shift ends at {effective_end_str}. "
                            f"You still have {minutes_remaining} minute(s) left. "
                            "Submit an early-exit request — admin approval is required to clock out before shift end."
                        ),
                        "shift_end": effective_end_str,
                        "minutes_remaining": minutes_remaining,
                        "is_half_day": half_day is not None,
                        "half_day_which": half_day.which_half.value if half_day else None,
                    },
                )
        return

    if expected_type == PunchType.BREAK_START:
        used = _break_used_minutes_today(today_punches, now_local)
        cap = int(shift.break_minutes or 0)
        if cap and used >= cap:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "BREAK_LIMIT_REACHED",
                    "message": f"You've used all {cap} break minutes for today.",
                    "break_used_minutes": used,
                    "break_max_minutes": cap,
                },
            )
        windows = shift.break_windows or []
        if windows:
            current_t = now_local.time()
            within = False
            for w in windows:
                try:
                    if _hhmm_to_time(w["start_time"]) <= current_t <= _hhmm_to_time(w["end_time"]):
                        within = True
                        break
                except Exception:
                    continue
            if not within:
                pretty = ", ".join(f"{w['label']} {w['start_time']}–{w['end_time']}" for w in windows)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OUTSIDE_BREAK_WINDOW",
                        "message": f"Break can only be taken during: {pretty}.",
                        "windows": windows,
                        "now_time": current_t.strftime("%H:%M"),
                    },
                )


def _holiday_for(db: Session, emp: Employee, on_date: date) -> Optional[Holiday]:
    """Return the active (non-restricted) holiday that applies to this employee
    on `on_date`, honouring location scoping — or None. Mirrors the detection
    used by /me/today and daily_rollup so all three agree."""
    from app.models.hr.holiday import HolidayType
    rows = (
        db.query(Holiday)
        .filter(
            Holiday.date == on_date,
            Holiday.is_active == True,            # noqa: E712
            Holiday.is_deleted == False,          # noqa: E712
            Holiday.holiday_type != HolidayType.RESTRICTED,
        )
        .all()
    )
    for h in rows:
        if h.location_id is None or (emp and emp.work_location_id == h.location_id):
            return h
    return None


def _approved_wfh_today(db: Session, employee_id: UUID, on_date: date) -> bool:
    """True when an APPROVED WFH/remote request covers `on_date`. Lets a
    legitimately-approved remote worker punch even on a rest day."""
    from app.models.hr.wfh_request import WfhRequest, WfhStatus
    for w in (
        db.query(WfhRequest)
        .filter(
            WfhRequest.employee_id == employee_id,
            WfhRequest.status == WfhStatus.APPROVED,
            WfhRequest.is_deleted == False,       # noqa: E712
            WfhRequest.wfh_date <= on_date,
        )
        .all()
    ):
        if w.wfh_date <= on_date <= (w.wfh_date_until or w.wfh_date):
            return True
    return False


def _approved_full_day_leave(db: Session, employee_id: UUID, on_date: date):
    """Return the APPROVED *full-day* leave covering `on_date`, or None.

    Half-day leave is intentionally excluded — the employee still works the
    other half, so clock-in stays allowed (daily_rollup tags it HALF_DAY).
    Mirrors the leave detection in app.utils.hr.attendance_logic.daily_rollup."""
    from app.models.hr.leave_request import LeaveRequest
    from app.models.hr.leave_type import LeaveStatus as _LeaveStatus
    return (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.from_date <= on_date,
            LeaveRequest.to_date >= on_date,
            LeaveRequest.status == _LeaveStatus.APPROVED,
            LeaveRequest.is_half_day == False,  # noqa: E712
            LeaveRequest.is_deleted == False,   # noqa: E712
        )
        .order_by(LeaveRequest.created_at.desc())
        .first()
    )


def _assert_self_punch_allowed_today(db: Session, emp: Employee, shift: Optional[Shift], today: date) -> None:
    """Block a *self* opening clock-in on a non-working day — full-day approved
    leave, weekly-off, or company holiday. Working such a day is an
    admin-recorded exception (cancel the leave, or admin punch-on-behalf +
    comp-off grant), not a self-punch.

    An approved WFH/remote waives the week-off / holiday block, but NOT the
    leave block — being officially on leave outranks a remote-work approval.
    Only gates the opening IN punch; an already-open day can still be
    broken/closed so nobody gets stuck mid-day."""
    # Full-day approved leave — most specific reason, checked first and not
    # waivable by a WFH approval.
    leave = _approved_full_day_leave(db, emp.id, today)
    if leave is not None:
        lt = leave.leave_type.value if hasattr(leave.leave_type, "value") else str(leave.leave_type)
        raise HTTPException(
            status_code=423,
            detail={
                "code": "ON_LEAVE_NO_PUNCH",
                "message": (
                    f"You're on approved {lt.replace('_', ' ').title()} leave today "
                    f"({leave.reference_no}) — self clock-in is disabled. If you actually "
                    "worked, ask HR to cancel the leave or record attendance on your behalf."
                ),
                "leave_type": lt,
                "reference_no": leave.reference_no,
            },
        )
    if _approved_wfh_today(db, emp.id, today):
        return
    if shift and today.weekday() in (shift.weekly_off_days or []):
        raise HTTPException(
            status_code=423,
            detail={
                "code": "WEEK_OFF_NO_PUNCH",
                "message": (
                    "Today is your weekly off — self clock-in is disabled. "
                    "If you worked, ask HR to record it and grant comp-off."
                ),
            },
        )
    hol = _holiday_for(db, emp, today)
    if hol is not None:
        raise HTTPException(
            status_code=423,
            detail={
                "code": "HOLIDAY_NO_PUNCH",
                "message": (
                    f"Today is a company holiday ({hol.name}) — self clock-in is disabled. "
                    "If you worked, ask HR to record it and grant comp-off."
                ),
            },
        )


def _create_punch(
    db: Session,
    emp: Employee,
    payload: PunchCreate,
    expected_type: PunchType,
    guard_open_in_required: bool,
) -> AttendancePunch:
    """Shared punch creator with idempotency + geofence verification."""
    today = date.today()
    # IST-bounded day window (punches are stored UTC; the business clock is IST).
    today_start_utc = datetime.combine(today, dtime.min, tzinfo=IST).astimezone(timezone.utc)
    tomorrow_start_utc = datetime.combine(today + timedelta(days=1), dtime.min, tzinfo=IST).astimezone(timezone.utc)
    punches = (
        db.query(AttendancePunch)
        .filter(AttendancePunch.employee_id == emp.id,
                AttendancePunch.punch_time >= today_start_utc,
                AttendancePunch.punch_time <  tomorrow_start_utc)
        .order_by(AttendancePunch.punch_time.asc())
        .all()
    )
    has_open_in = False
    has_close_out = False
    on_break = False
    for p in punches:
        if p.punch_type == PunchType.IN:
            has_open_in = True
            on_break = False
        elif p.punch_type == PunchType.OUT:
            has_close_out = True
            has_open_in = False
        elif p.punch_type == PunchType.BREAK_START:
            on_break = True
        elif p.punch_type == PunchType.BREAK_END:
            on_break = False

    if expected_type == PunchType.IN:
        if has_open_in:
            raise HTTPException(409, "Already clocked in today")
        if has_close_out:
            raise HTTPException(409, "Already clocked out for today; corrections needed for re-entry")
    elif expected_type == PunchType.OUT:
        if not has_open_in:
            raise HTTPException(409, "Not clocked in")
    elif expected_type == PunchType.BREAK_START:
        if not has_open_in or on_break:
            raise HTTPException(409, "Cannot start a break in current state")
    elif expected_type == PunchType.BREAK_END:
        if not on_break:
            raise HTTPException(409, "No break in progress")

    # Shift-policy validation (early/late punch, early exit, break windows, break cap).
    shift = resolve_shift(db, emp.id, today)
    # Self clock-in is not allowed on a weekly-off / company-holiday day.
    if expected_type == PunchType.IN:
        _assert_self_punch_allowed_today(db, emp, shift, today)
    _validate_punch_policy(shift, expected_type, punches, db=db, employee_id=emp.id)

    geo = verify_geofence(db, emp.id, payload.geo_lat, payload.geo_lng)
    flagged = (payload.geo_lat is not None and payload.geo_lng is not None) and not geo.verified

    # ──────────────────────────────────────────────────────────────────
    # ENFORCE GEO-FENCE POLICY at punch time.
    # By default, an out-of-fence punch is BLOCKED with 403. The block is
    # waived only when the employee has an APPROVED WFH or REMOTE request
    # covering today's date — that's the workflow the admin signed off on
    # via the /admin/hr/attendance/{wfh,remote} tabs.
    # Punches without lat/lng (kiosk / biometric / admin-on-behalf) skip
    # the check; the device is the source of truth there.
    # ──────────────────────────────────────────────────────────────────
    if flagged:
        from app.models.hr.wfh_request import WfhRequest, WfhStatus
        wfh_match = None
        for w in (
            db.query(WfhRequest)
            .filter(
                WfhRequest.employee_id == emp.id,
                WfhRequest.status == WfhStatus.APPROVED,
                WfhRequest.is_deleted == False,  # noqa: E712
                WfhRequest.wfh_date <= today,
            )
            .all()
        ):
            until = w.wfh_date_until or w.wfh_date
            if w.wfh_date <= today <= until:
                wfh_match = w
                break
        if wfh_match is None:
            # No approval — block with a structured error the frontend can
            # turn into a friendly toast that points at the Remote tab.
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "geofence_blocked",
                    "message": (
                        "You are outside your office geo-fence. "
                        "Request a Remote-day or WFH approval before punching in from this location."
                    ),
                    "distance_m": geo.distance_m,
                },
            )
        # Approval covers today → treat as verified for both audit + rollup.
        flagged = False
        geo = GeoVerifyResult(verified=True, fence_id=geo.fence_id, distance_m=geo.distance_m)

    punch = AttendancePunch(
        employee_id=emp.id,
        punch_time=datetime.now(timezone.utc),
        punch_type=expected_type,
        source=payload.source,
        geo_lat=payload.geo_lat,
        geo_lng=payload.geo_lng,
        geo_verified=geo.verified,
        geo_distance_m=geo.distance_m,
        device_id=payload.device_info,
        selfie_url=payload.selfie_url,
        payload={"justification": payload.justification} if payload.justification else {},
    )
    db.add(punch)
    db.flush()

    att = daily_rollup(db, emp.id, today, actor_id=None, source=payload.source)
    if flagged:
        att.is_flagged = True
        if payload.justification:
            existing_remarks = att.remarks or ""
            att.remarks = (existing_remarks + ("\n" if existing_remarks else "") + f"Geofence-out: {payload.justification}").strip()
    log(
        db,
        actor_id=None,
        action=AttendanceLogAction.PUNCH,
        target_table="hr_attendance_punches",
        target_id=punch.id,
        employee_id=emp.id,
        payload={
            "type": expected_type.value,
            "geo_verified": geo.verified,
            "distance_m": geo.distance_m,
            "source": payload.source.value,
        },
    )
    db.commit()
    db.refresh(punch)
    return punch


@router.post("/me/clock-in", response_model=PunchResponse, status_code=http_status.HTTP_201_CREATED)
def me_clock_in(
    payload: PunchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    payload.punch_type = PunchType.IN
    return PunchResponse.model_validate(_create_punch(db, emp, payload, PunchType.IN, False))


@router.post("/me/clock-out", response_model=PunchResponse, status_code=http_status.HTTP_201_CREATED)
def me_clock_out(
    payload: PunchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    payload.punch_type = PunchType.OUT
    return PunchResponse.model_validate(_create_punch(db, emp, payload, PunchType.OUT, True))


@router.post("/me/break/start", response_model=PunchResponse, status_code=http_status.HTTP_201_CREATED)
def me_break_start(
    payload: PunchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    payload.punch_type = PunchType.BREAK_START
    return PunchResponse.model_validate(_create_punch(db, emp, payload, PunchType.BREAK_START, True))


@router.post("/me/break/end", response_model=PunchResponse, status_code=http_status.HTTP_201_CREATED)
def me_break_end(
    payload: PunchCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    payload.punch_type = PunchType.BREAK_END
    return PunchResponse.model_validate(_create_punch(db, emp, payload, PunchType.BREAK_END, True))


@router.get("/me/history", response_model=MyHistoryResponse)
def me_history(
    days: int = Query(14, ge=1, le=90),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    end = date.today()
    start = end - timedelta(days=days - 1)
    rows = (
        db.query(Attendance)
        .filter(
            Attendance.employee_id == emp.id,
            Attendance.date >= start, Attendance.date <= end,
            Attendance.is_deleted == False,  # noqa: E712
        )
        .order_by(Attendance.date.desc())
        .all()
    )
    items = [
        MyHistoryDay(
            date=r.date, status=r.status, working_hours=float(r.working_hours or 0),
            check_in_time=r.check_in_time, check_out_time=r.check_out_time,
        ) for r in rows
    ]
    return MyHistoryResponse(items=items)


@router.get("/me/month", response_model=MyMonthResponse)
def me_month(
    year: int = Query(..., ge=2000, le=2100),
    month: int = Query(..., ge=1, le=12),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)

    # Self-heal — same rationale as /me/day-detail: close any forgotten
    # clock-outs before reading so month-grid cell tooltips show real
    # working_hours instead of 0.0h on auto-closed days.
    if finalize_orphan_open_punches(db, emp.id, lookback_days=14, actor_id=None):
        db.commit()

    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    rows = (
        db.query(Attendance)
        .filter(
            Attendance.employee_id == emp.id,
            Attendance.date >= start, Attendance.date <= end,
            Attendance.is_deleted == False,  # noqa: E712
        )
        .order_by(Attendance.date.asc())
        .all()
    )
    return MyMonthResponse(
        year=year, month=month,
        cells=[
            MyMonthCell(date=r.date, status=r.status, working_hours=float(r.working_hours or 0))
            for r in rows
        ],
    )


@router.post("/me/request-early-exit", response_model=EarlyExitRequestResponse, status_code=http_status.HTTP_201_CREATED)
def me_request_early_exit(
    body: EarlyExitRequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit an early-exit approval request when clock-out is locked because
    the shift hasn't ended yet.

    Creates an AttendanceCorrection tagged `[EARLY_EXIT]` in the reason. Once
    admin approves it via the existing correction-approve flow, the user can
    clock out — `_validate_punch_policy` checks for an approved
    `[EARLY_EXIT]` correction and lets the OUT punch through.
    """
    emp = _resolve_self_employee(db, user)
    today = date.today()
    shift = resolve_shift(db, emp.id, today)
    now_local = _now_local()
    now_utc = datetime.now(timezone.utc)

    # Block duplicate pending requests for the same day.
    existing = (
        db.query(AttendanceCorrection)
        .filter(
            AttendanceCorrection.employee_id == emp.id,
            AttendanceCorrection.attendance_date == today,
            AttendanceCorrection.status == CorrectionStatus.PENDING,
            AttendanceCorrection.reason.like("[EARLY_EXIT]%"),
        )
        .first()
    )
    if existing:
        raise HTTPException(409, "You already have a pending early-exit request for today")

    minutes_remaining = 0
    if shift:
        minutes_remaining = max(0, int((_shift_end_local(shift, today) - now_local).total_seconds() // 60))
    tag = f"[EARLY_EXIT] (leaving with {minutes_remaining} min left of shift) "
    reason = (tag + body.reason).strip()[:1000]

    corr = AttendanceCorrection(
        employee_id=emp.id,
        attendance_id=None,
        attendance_date=today,
        original_check_in=None,
        original_check_out=None,
        requested_check_in=None,
        requested_check_out=now_utc,
        reason=reason,
        status=CorrectionStatus.PENDING,
    )
    db.add(corr)
    db.flush()
    log(
        db,
        actor_id=None,
        action=AttendanceLogAction.CORRECTION_REQUESTED,
        target_table="hr_attendance_corrections",
        target_id=corr.id,
        employee_id=emp.id,
        payload={"kind": "EARLY_EXIT", "minutes_remaining": minutes_remaining, "reason": body.reason},
    )
    db.commit()
    db.refresh(corr)
    return EarlyExitRequestResponse(
        correction_id=corr.id,
        attendance_date=corr.attendance_date,
        reason=corr.reason,
        status=corr.status.value,
        minutes_remaining=minutes_remaining,
        created_at=corr.created_at,
    )


@router.post("/me/request-late-punch", response_model=LatePunchRequestResponse, status_code=http_status.HTTP_201_CREATED)
def me_request_late_punch(
    body: LatePunchRequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Submit a late-punch approval request when self-punch is locked by policy.

    Creates an AttendanceCorrection with the requested_check_in set to "now".
    On admin approval, the existing correction-approve handler will write the
    actual IN punch and trigger the daily rollup, marking the day as LATE.
    """
    emp = _resolve_self_employee(db, user)
    today = date.today()
    shift = resolve_shift(db, emp.id, today)
    now_local = _now_local()
    now_utc = datetime.now(timezone.utc)

    # Prevent duplicate pending requests for the same day.
    existing = (
        db.query(AttendanceCorrection)
        .filter(
            AttendanceCorrection.employee_id == emp.id,
            AttendanceCorrection.attendance_date == today,
            AttendanceCorrection.status == CorrectionStatus.PENDING,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, "You already have a pending request for today")

    # Use effective shift start so a half-day-off employee's audit reason
    # records the *real* late minutes (vs midshift), not 200+ minutes past 9 AM.
    if shift:
        _hd = _approved_half_day(db, emp.id, today)
        _eff_start, _eff_end = _effective_shift_window(shift, today, _hd)
        minutes_late = _minutes_late_now(shift, now_local, effective_start=_eff_start)
        # Enforce the same cap the /me/today UI honours: no self late-punch
        # request once past the 2h half-day cutoff or after the shift has
        # ended. Beyond that it's a half-day/no-show that needs admin
        # regularization, not a quick late-punch.
        _eff_span_min = (_eff_end - _eff_start).total_seconds() / 60.0
        if _eff_span_min <= 0:
            _eff_span_min += 24 * 60
        _cutoff_min = int(min(_eff_span_min / 2.0, 120.0))
        if minutes_late > _cutoff_min or now_local >= _eff_end:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "LATE_PUNCH_WINDOW_CLOSED",
                    "message": (
                        f"The self late-punch window has closed — you are {minutes_late} minutes late "
                        f"(cap {_cutoff_min} min"
                        + (", and your shift has already ended" if now_local >= _eff_end else "")
                        + "). Contact HR to regularize today's attendance."
                    ),
                    "minutes_late": minutes_late,
                    "cutoff_minutes": _cutoff_min,
                    "shift_ended": now_local >= _eff_end,
                },
            )
    else:
        minutes_late = 0
    tag = f"[LATE_PUNCH] (late by {minutes_late} min) "
    reason = (tag + body.reason).strip()[:1000]

    corr = AttendanceCorrection(
        employee_id=emp.id,
        attendance_id=None,
        attendance_date=today,
        original_check_in=None,
        original_check_out=None,
        requested_check_in=now_utc,
        requested_check_out=None,
        reason=reason,
        status=CorrectionStatus.PENDING,
    )
    db.add(corr)
    db.flush()
    log(
        db,
        actor_id=None,
        action=AttendanceLogAction.CORRECTION_REQUESTED,
        target_table="hr_attendance_corrections",
        target_id=corr.id,
        employee_id=emp.id,
        payload={"kind": "LATE_PUNCH", "minutes_late": minutes_late, "reason": body.reason},
    )
    db.commit()
    db.refresh(corr)
    return LatePunchRequestResponse(
        correction_id=corr.id,
        attendance_date=corr.attendance_date,
        requested_check_in=corr.requested_check_in,
        reason=corr.reason,
        status=corr.status.value,
        minutes_late=minutes_late,
        created_at=corr.created_at,
    )


@router.get("/me/punches", response_model=PunchListResponse)
def me_punches(
    days: int = Query(7, ge=1, le=30),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self_employee(db, user)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(AttendancePunch)
        .filter(AttendancePunch.employee_id == emp.id, AttendancePunch.punch_time >= cutoff)
        .order_by(AttendancePunch.punch_time.desc())
        .all()
    )
    return PunchListResponse(items=[PunchResponse.model_validate(p) for p in rows])


@router.get("/me/day-detail", response_model=MyDayDetailResponse)
def me_day_detail(
    on_date: date = Query(..., description="IST calendar date (YYYY-MM-DD)"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Per-day attendance report: rolled-up totals plus the raw punch tape
    and computed break segments. Powers the self-service Attendance Report
    drawer — surfaces clock-in/out, every break with duration, overtime,
    late minutes, and whether the row was auto-closed by the orphan-punch
    finalizer (so the user knows to request a correction if they need it
    extended).
    """
    emp = _resolve_self_employee(db, user)

    # Self-heal — close any forgotten clock-outs from past days before reading
    # so the report modal always shows the auto-finalized state, not stale
    # "still open" values. Without this, the modal would show last_clock_out
    # as `--:--` and working_hours as 0 even after the day rolled over.
    if finalize_orphan_open_punches(db, emp.id, lookback_days=7, actor_id=None):
        db.commit()

    # Build IST-bounded day window and pull punches in that range.
    day_start_ist = datetime.combine(on_date, dtime.min, tzinfo=IST)
    day_end_ist = datetime.combine(on_date + timedelta(days=1), dtime.min, tzinfo=IST)
    day_start_utc = day_start_ist.astimezone(timezone.utc)
    day_end_utc = day_end_ist.astimezone(timezone.utc)

    att = (
        db.query(Attendance)
        .filter(Attendance.employee_id == emp.id, Attendance.date == on_date,
                Attendance.is_deleted == False)  # noqa: E712
        .first()
    )
    shift = resolve_shift(db, emp.id, on_date)
    punches = (
        db.query(AttendancePunch)
        .filter(
            AttendancePunch.employee_id == emp.id,
            AttendancePunch.punch_time >= day_start_utc,
            AttendancePunch.punch_time <  day_end_utc,
        )
        .order_by(AttendancePunch.punch_time.asc())
        .all()
    )

    # AUTO_CHECKOUT detection — surfaces a banner in the UI.
    auto_closed = False
    if att:
        from app.models.hr.attendance_log import AttendanceLog
        auto_log = (
            db.query(AttendanceLog.id)
            .filter(AttendanceLog.target_id == att.id,
                    AttendanceLog.action == AttendanceLogAction.AUTO_CHECKOUT)
            .first()
        )
        auto_closed = auto_log is not None

    # Break segments — pair BREAK_START → BREAK_END; if the day ended on an
    # open BREAK_START (extremely rare after the auto-finalizer ran), report
    # it as an open segment so the UI can render it with an "open" badge.
    breaks: list = []
    open_start = None
    for p in punches:
        if p.punch_type == PunchType.BREAK_START:
            open_start = p
        elif p.punch_type == PunchType.BREAK_END and open_start is not None:
            dur_min = (p.punch_time - open_start.punch_time).total_seconds() / 60.0
            breaks.append(MyDayBreakSegment(
                start=open_start.punch_time, end=p.punch_time,
                minutes=round(max(0.0, dur_min), 1), is_open=False,
            ))
            open_start = None
    if open_start is not None:
        breaks.append(MyDayBreakSegment(
            start=open_start.punch_time, end=None, minutes=0.0, is_open=True,
        ))

    def _is_auto(p: AttendancePunch) -> bool:
        payload = p.payload or {}
        return bool(payload.get("auto") is True)

    return MyDayDetailResponse(
        date=on_date,
        status=att.status if att else AttendanceStatus.ABSENT,
        shift=_to_shift_response(shift) if shift else None,
        check_in_time=att.check_in_time if att else None,
        check_out_time=att.check_out_time if att else None,
        working_hours=float(att.working_hours or 0) if att else 0.0,
        break_hours=float(att.break_hours or 0) if att else 0.0,
        break_count=len(breaks),
        late_minutes=int(att.late_minutes or 0) if att else 0,
        early_exit_minutes=int(att.early_exit_minutes or 0) if att else 0,
        overtime_hours=float(att.overtime_hours or 0) if att else 0.0,
        is_flagged=bool(att.is_flagged) if att else False,
        is_locked=bool(att.is_locked) if att else False,
        is_auto_closed=auto_closed,
        lop_days=float(att.lop_days or 0) if att else 0.0,
        remarks=att.remarks if att else None,
        punches=[
            MyDayPunch(
                id=p.id, punch_time=p.punch_time, punch_type=p.punch_type,
                geo_verified=bool(p.geo_verified), source=p.source,
                is_auto=_is_auto(p),
            ) for p in punches
        ],
        breaks=breaks,
    )
