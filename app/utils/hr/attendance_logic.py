"""HR Attendance — business logic helpers.

Pure-ish functions for shift resolution, status computation, geofence
verification, and the daily rollup that feeds `hr_attendance` from the
append-only `hr_attendance_punches` log.

Design notes:
- `compute_attendance_status` is **pure** (no DB). Easy to unit test.
- `daily_rollup` is idempotent — safe to call repeatedly for the same
  (employee_id, date). Preserves `remarks` and `is_locked` across calls.
- All admin-driven mutations route through `_log()` so we keep an immutable
  audit trail in `hr_attendance_logs`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.hr.employee import Employee
from app.models.hr.shift import Shift, EmployeeShiftAssignment
from app.models.hr.attendance import Attendance, AttendanceStatus, AttendanceSource
from app.models.hr.attendance_punch import AttendancePunch, PunchType
from app.models.hr.wfh_request import WfhRequest, WfhStatus
from app.models.hr.holiday import Holiday
from app.models.hr.attendance_log import AttendanceLog, AttendanceLogAction
from app.models.hr.geo_fence import GeoFence


# Business timezone — all wall-clock comparisons (shift start/end, late minutes,
# half-day cutoff) happen in IST. Storage stays UTC; only the comparison frame
# moves. India doesn't observe DST so a static UTC+5:30 offset is correct.
IST = timezone(timedelta(hours=5, minutes=30))


# ──────────────────────────────────────────────────────────────────────────
# Audit helper
# ──────────────────────────────────────────────────────────────────────────

def log(
    db: Session,
    *,
    actor_id: Optional[UUID],
    action: AttendanceLogAction,
    target_table: Optional[str] = None,
    target_id: Optional[UUID] = None,
    employee_id: Optional[UUID] = None,
    payload: Optional[dict] = None,
) -> None:
    """Append an audit row. Caller is responsible for db.commit()."""
    db.add(AttendanceLog(
        actor_user_id=actor_id,
        action=action,
        target_table=target_table,
        target_id=target_id,
        employee_id=employee_id,
        payload=payload or {},
    ))


# ──────────────────────────────────────────────────────────────────────────
# Shift resolution
# ──────────────────────────────────────────────────────────────────────────

def resolve_shift(db: Session, employee_id: UUID, on_date: date) -> Optional[Shift]:
    """Active shift for an employee on a date.

    Looks up the most recent `EmployeeShiftAssignment` whose effective window
    covers `on_date`. Falls back to `Employee.shift_id` (denormalised default).
    """
    q = (
        db.query(EmployeeShiftAssignment)
        .filter(
            EmployeeShiftAssignment.employee_id == employee_id,
            EmployeeShiftAssignment.effective_from <= on_date,
        )
        .order_by(EmployeeShiftAssignment.effective_from.desc())
    )
    for row in q.all():
        if row.effective_until is None or row.effective_until >= on_date:
            shift = db.query(Shift).filter(Shift.id == row.shift_id).first()
            if shift and not shift.is_deleted:
                return shift
            break
    # Fallback — Employee.shift_id denormalisation
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    if emp and emp.shift_id:
        shift = db.query(Shift).filter(Shift.id == emp.shift_id).first()
        if shift and not shift.is_deleted:
            return shift
    return None


# ──────────────────────────────────────────────────────────────────────────
# Pure status / hours computation
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class ComputeResult:
    status: AttendanceStatus
    check_in_time: Optional[datetime]
    check_out_time: Optional[datetime]
    working_hours: float
    break_hours: float
    late_minutes: int
    early_exit_minutes: int
    overtime_hours: float


def _combine(d: date, t: time) -> datetime:
    """Build a tz-aware datetime in IST (business clock).

    Shift `start_time` / `end_time` columns are stored as naive wall-clock IST
    values (e.g. 09:00 means 9 AM IST), so any comparison against a UTC-stored
    punch timestamp must anchor the shift moment in IST. Returning the
    datetime with `tzinfo=IST` lets aware-aware subtraction normalize to UTC
    correctly and produces the right late/early/overtime deltas.
    """
    return datetime.combine(d, t, tzinfo=IST)


def _day_bounds_utc(d: date) -> tuple[datetime, datetime]:
    """UTC range that covers the IST calendar day `d`.

    A day in IST (00:00–23:59:59) spans from `d 00:00 IST` to `d+1 00:00 IST`,
    which in UTC is `d-1 18:30 UTC` to `d 18:30 UTC`. The punch table is keyed
    on UTC timestamps, so querying "punches on date d (IST)" requires this
    shifted range — using `d 00:00 UTC` to `d+1 00:00 UTC` would miss the
    18:30–24:00 IST window and double-count the next day's morning.
    """
    start_ist = datetime.combine(d, time.min, tzinfo=IST)
    end_ist = datetime.combine(d + timedelta(days=1), time.min, tzinfo=IST)
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def _build_work_intervals(punches_sorted: List[AttendancePunch]) -> list:
    """Return list of (start, end) datetime tuples for actual-work segments.

    A work segment is the time between an IN (or BREAK_END) and the next
    BREAK_START (or OUT). Breaks themselves are NOT work and are excluded.
    Used by the OT computation so post-shift time spent on break doesn't
    inflate overtime hours.
    """
    intervals = []
    cur_start = None
    for p in punches_sorted:
        if p.punch_type in (PunchType.IN, PunchType.BREAK_END):
            if cur_start is None:
                cur_start = p.punch_time
        elif p.punch_type in (PunchType.OUT, PunchType.BREAK_START):
            if cur_start is not None and p.punch_time > cur_start:
                intervals.append((cur_start, p.punch_time))
            cur_start = None
    return intervals


def compute_attendance_status(
    punches: List[AttendancePunch],
    shift: Optional[Shift],
    on_date: date,
) -> ComputeResult:
    """Pure status computation from raw punches.

    First IN  → check_in_time
    Last OUT  → check_out_time
    Sum BREAK_START/BREAK_END pairs → break_hours
    working_hours = (check_out - check_in) - break_hours

    LATE if check_in > shift.start_time + grace_minutes
    HALF_DAY if working_hours < shift.half_day_hours

    Overtime: segment-aware post-shift detection
        — wall-clock time worked past shift_end + grace (breaks excluded)
        — plus wall-clock time worked before shift_start - grace (rare, pre-OT)
        — floored by max(0, working_hours - full_day_hours) for sanity
    This captures the corporate-standard case where an employee clocks out
    past shift end and gets credit for the extra time, without inflating
    OT by counting break minutes that happen to fall after shift end.
    """
    if not punches:
        return ComputeResult(
            status=AttendanceStatus.ABSENT,
            check_in_time=None, check_out_time=None,
            working_hours=0.0, break_hours=0.0,
            late_minutes=0, early_exit_minutes=0, overtime_hours=0.0,
        )

    punches_sorted = sorted(punches, key=lambda p: p.punch_time)
    ins  = [p for p in punches_sorted if p.punch_type == PunchType.IN]
    outs = [p for p in punches_sorted if p.punch_type == PunchType.OUT]

    check_in_time = ins[0].punch_time if ins else None
    check_out_time = outs[-1].punch_time if outs else None

    # Sum break pairs in order
    break_seconds = 0
    open_break_start: Optional[datetime] = None
    for p in punches_sorted:
        if p.punch_type == PunchType.BREAK_START:
            open_break_start = p.punch_time
        elif p.punch_type == PunchType.BREAK_END and open_break_start is not None:
            delta = (p.punch_time - open_break_start).total_seconds()
            if delta > 0:
                break_seconds += delta
            open_break_start = None

    break_hours = break_seconds / 3600.0

    working_hours = 0.0
    if check_in_time and check_out_time and check_out_time > check_in_time:
        total = (check_out_time - check_in_time).total_seconds() / 3600.0
        working_hours = max(0.0, total - break_hours)

    # Status determination
    late_minutes = 0
    early_exit_minutes = 0
    overtime_hours = 0.0

    if shift:
        shift_start_dt = _combine(on_date, shift.start_time)
        shift_end_dt   = _combine(on_date, shift.end_time)
        grace = int(shift.grace_minutes or 0)
        full_day = float(shift.full_day_hours or 8.0)
        half_day = float(shift.half_day_hours or 4.0)

        if check_in_time:
            delta_min = (check_in_time - shift_start_dt).total_seconds() / 60.0
            late_minutes = max(0, int(delta_min - grace))
        if check_out_time and check_out_time < shift_end_dt:
            early_exit_minutes = max(0, int((shift_end_dt - check_out_time).total_seconds() / 60.0))

        # ── Corporate-grade OT detection ────────────────────────────────
        # Auto-OT = actual work time past shift_end + grace (breaks excluded)
        #         + actual work time before shift_start - grace (rare; pre-OT)
        # Floor at max(0, worked - full_day) so the legacy "long total" path
        # still gives OT when shifts are short and continuous work was long.
        from datetime import timedelta as _td
        post_threshold = shift_end_dt + _td(minutes=grace)
        pre_threshold  = shift_start_dt - _td(minutes=grace)
        post_seconds = 0.0
        pre_seconds  = 0.0
        for w_start, w_end in _build_work_intervals(punches_sorted):
            # Post-shift portion of this segment
            ov_start = max(w_start, post_threshold)
            if w_end > ov_start:
                post_seconds += (w_end - ov_start).total_seconds()
            # Pre-shift portion
            ov_end = min(w_end, pre_threshold)
            if ov_end > w_start:
                pre_seconds += (ov_end - w_start).total_seconds()
        detected_ot = (post_seconds + pre_seconds) / 3600.0
        sanity_ot = max(0.0, working_hours - full_day)
        overtime_hours = max(detected_ot, sanity_ot)

        if not check_in_time:
            status = AttendanceStatus.ABSENT
        elif not check_out_time:
            # Still logged in — don't penalise the partial day as HALF_DAY
            # until the session is actually closed. Late vs Present is still
            # determined by check-in punctuality.
            status = AttendanceStatus.LATE if late_minutes > 0 else AttendanceStatus.PRESENT
        elif working_hours < half_day:
            status = AttendanceStatus.HALF_DAY
        elif late_minutes > 0:
            status = AttendanceStatus.LATE
        else:
            status = AttendanceStatus.PRESENT
    else:
        # No shift assigned — generic 8h yardstick
        if not check_in_time:
            status = AttendanceStatus.ABSENT
        elif not check_out_time:
            status = AttendanceStatus.PRESENT
        elif working_hours < 4.0:
            status = AttendanceStatus.HALF_DAY
        else:
            status = AttendanceStatus.PRESENT
        overtime_hours = max(0.0, working_hours - 8.0)

    return ComputeResult(
        status=status,
        check_in_time=check_in_time,
        check_out_time=check_out_time,
        working_hours=round(working_hours, 2),
        break_hours=round(break_hours, 2),
        late_minutes=late_minutes,
        early_exit_minutes=early_exit_minutes,
        overtime_hours=round(overtime_hours, 2),
    )


# ──────────────────────────────────────────────────────────────────────────
# Geofence verification
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class GeoVerifyResult:
    verified: bool
    fence_id: Optional[UUID]
    distance_m: Optional[float]


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6_371_000  # earth radius in meters
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def verify_geofence(
    db: Session,
    employee_id: UUID,
    lat: Optional[float],
    lng: Optional[float],
) -> GeoVerifyResult:
    """Returns verified=True if the punch is within any active geo-fence.

    Resolution order:
      1. Fences tied to the employee's work_location_id (specific match).
      2. Fall back to ALL active fences without a location_id (org-wide).
      3. As a last resort, ALL active fences (so a single office fence
         verifies every employee even if nobody has work_location set).

    Defaults to verified=True only when lat/lng are missing OR there are
    no fences at all configured in the system.
    """
    if lat is None or lng is None:
        return GeoVerifyResult(verified=True, fence_id=None, distance_m=None)

    emp = db.query(Employee).filter(Employee.id == employee_id).first()

    base = db.query(GeoFence).filter(
        GeoFence.is_active == True,  # noqa: E712
        GeoFence.is_deleted == False,  # noqa: E712
    )

    fences = []
    if emp and emp.work_location_id:
        fences = base.filter(GeoFence.location_id == emp.work_location_id).all()
    if not fences:
        # Org-wide fences (no location tie) — typical for the default office.
        fences = base.filter(GeoFence.location_id.is_(None)).all()
    if not fences:
        # Last resort: any active fence in the system. A single office fence
        # should verify every employee even if no work locations are set up.
        fences = base.all()

    if not fences:
        # System has no fences at all — pass through with verified=True so we
        # don't block punches in early-setup tenants.
        return GeoVerifyResult(verified=True, fence_id=None, distance_m=None)

    closest_fence: Optional[GeoFence] = None
    closest_dist: float = float("inf")
    verified = False

    for fence in fences:
        try:
            d = _haversine_m(lat, lng, float(fence.center_lat), float(fence.center_lng))
        except (TypeError, ValueError):
            continue
        if d < closest_dist:
            closest_dist = d
            closest_fence = fence
        if d <= float(fence.radius_meters):
            verified = True

    return GeoVerifyResult(
        verified=verified,
        fence_id=closest_fence.id if closest_fence else None,
        distance_m=round(closest_dist, 2) if closest_dist != float("inf") else None,
    )


# ──────────────────────────────────────────────────────────────────────────
# Daily rollup — heart of the system, idempotent
# ──────────────────────────────────────────────────────────────────────────

def daily_rollup(
    db: Session,
    employee_id: UUID,
    on_date: date,
    *,
    actor_id: Optional[UUID] = None,
    source: AttendanceSource = AttendanceSource.SYSTEM,
) -> Attendance:
    """Recompute the Attendance row for (employee_id, on_date) from punches.

    Honours short-circuits:
      - approved WFH covering on_date → status=WFH
      - active holiday on on_date for the employee's location → status=HOLIDAY
      - on_date.weekday() in shift.weekly_off_days → status=WEEK_OFF
    Preserves `remarks` and `is_locked` if a row already exists.
    Links all punches for the day back to the resulting Attendance row.
    """
    shift = resolve_shift(db, employee_id, on_date)

    # Short-circuit: approved WFH covering this date
    wfh_q = (
        db.query(WfhRequest)
        .filter(
            WfhRequest.employee_id == employee_id,
            WfhRequest.status == WfhStatus.APPROVED,
            WfhRequest.wfh_date <= on_date,
            WfhRequest.is_deleted == False,  # noqa: E712
        )
    )
    wfh_match = None
    for w in wfh_q.all():
        until = w.wfh_date_until or w.wfh_date
        if w.wfh_date <= on_date <= until:
            wfh_match = w
            break

    # Short-circuit: holiday
    # RESTRICTED holidays are optional — employees claim them from a pool
    # rather than getting them by default. We exclude them from the
    # auto-rollup so they don't silently turn a normal working day into a
    # paid HOLIDAY for everyone.
    from app.models.hr.holiday import HolidayType
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    holiday_q = db.query(Holiday).filter(
        Holiday.date == on_date,
        Holiday.is_active == True,   # noqa: E712
        Holiday.is_deleted == False,  # noqa: E712
        Holiday.holiday_type != HolidayType.RESTRICTED,
    )
    holiday_match = None
    for h in holiday_q.all():
        if h.location_id is None or (emp and emp.work_location_id == h.location_id):
            holiday_match = h
            break

    is_week_off = bool(shift and on_date.weekday() in (shift.weekly_off_days or []))

    day_start_utc, day_end_utc = _day_bounds_utc(on_date)
    punches = (
        db.query(AttendancePunch)
        .filter(
            AttendancePunch.employee_id == employee_id,
            AttendancePunch.punch_time >= day_start_utc,
            AttendancePunch.punch_time <  day_end_utc,
        )
        .order_by(AttendancePunch.punch_time.asc())
        .all()
    )

    # Short-circuit: approved Half-Day for this date
    # Imported locally so the half_day_request model isn't required at
    # module load time (keeps `daily_rollup` self-contained for tests).
    from app.models.hr.half_day_request import HalfDayRequest, HalfDayStatus
    half_day_match = (
        db.query(HalfDayRequest)
        .filter(
            HalfDayRequest.employee_id == employee_id,
            HalfDayRequest.half_day_date == on_date,
            HalfDayRequest.status == HalfDayStatus.APPROVED,
            HalfDayRequest.is_deleted == False,  # noqa: E712
        )
        .first()
    )

    # Compute baseline from punches, then optionally override status
    result = compute_attendance_status(punches, shift, on_date)

    if wfh_match:
        result.status = AttendanceStatus.WFH
        # Only credit a full WFH day's hours when the date has actually passed.
        # For today (in-progress) or future dates, leave working_hours at 0 so
        # the report modal / month tooltip don't show a phantom 8.00h before
        # the day has even happened. Past WFH days assume the employee worked
        # the full shift unless punches say otherwise.
        if not result.working_hours and shift and on_date < date.today():
            result.working_hours = float(shift.full_day_hours or 8.0)
    elif half_day_match:
        # Approved half-day overrides the punch-based status. If the employee
        # also punched in for the working half we keep the recorded
        # check_in/working_hours so payroll can credit the actual time.
        result.status = AttendanceStatus.HALF_DAY
        if not result.working_hours and shift:
            result.working_hours = float(shift.half_day_hours or 4.0)
    elif holiday_match:
        # Only override if there are no punches (employee may have worked OT)
        if not result.check_in_time:
            result.status = AttendanceStatus.HOLIDAY
    elif is_week_off and not result.check_in_time:
        result.status = AttendanceStatus.WEEK_OFF

    # Upsert the row
    existing = (
        db.query(Attendance)
        .filter(Attendance.employee_id == employee_id, Attendance.date == on_date)
        .first()
    )

    geo_lat = None
    geo_lng = None
    geo_verified = False
    if punches:
        first = punches[0]
        if first.geo_lat is not None and first.geo_lng is not None:
            geo_lat = first.geo_lat
            geo_lng = first.geo_lng
            geo_verified = bool(first.geo_verified)

    if existing:
        existing.shift_id = shift.id if shift else None
        existing.check_in_time = result.check_in_time
        existing.check_out_time = result.check_out_time
        existing.working_hours = result.working_hours
        existing.break_hours = result.break_hours
        existing.late_minutes = result.late_minutes
        existing.early_exit_minutes = result.early_exit_minutes
        existing.overtime_hours = result.overtime_hours
        existing.status = result.status
        if not existing.is_locked:
            existing.source = source
        if geo_lat is not None:
            existing.geo_lat = geo_lat
            existing.geo_lng = geo_lng
            existing.geo_verified = geo_verified
        att = existing
    else:
        att = Attendance(
            employee_id=employee_id,
            date=on_date,
            shift_id=shift.id if shift else None,
            check_in_time=result.check_in_time,
            check_out_time=result.check_out_time,
            working_hours=result.working_hours,
            break_hours=result.break_hours,
            late_minutes=result.late_minutes,
            early_exit_minutes=result.early_exit_minutes,
            overtime_hours=result.overtime_hours,
            status=result.status,
            source=source,
            geo_lat=geo_lat,
            geo_lng=geo_lng,
            geo_verified=geo_verified,
            created_by_id=actor_id,
            last_updated_by_id=actor_id,
        )
        db.add(att)
        db.flush()  # need att.id to link punches

    # Link punches back
    for p in punches:
        if p.attendance_id != att.id:
            p.attendance_id = att.id

    # ──────────────────────────────────────────────────────────────────
    # Auto-create an OvertimeRequest when the day's working hours exceed
    # the shift's full-day quota. The admin then approves/rejects this
    # entry from /admin/hr/attendance/overtime — no need for the employee
    # to manually submit it. Idempotent: re-running daily_rollup updates
    # the existing OT row instead of duplicating it.
    # ──────────────────────────────────────────────────────────────────
    # Only auto-create OT once the day has actually closed (clock-out exists).
    # Open in-progress rows shouldn't queue OT yet — we don't know if the
    # employee is genuinely working OT or just hasn't punched out.
    if (
        result.overtime_hours
        and result.overtime_hours > 0
        and result.check_out_time is not None
    ):
        from app.models.hr.overtime import OvertimeRequest, OtStatus, OtPayrollStatus, OtType
        existing_ot = (
            db.query(OvertimeRequest)
            .filter(
                OvertimeRequest.employee_id == employee_id,
                OvertimeRequest.date == on_date,
                OvertimeRequest.is_deleted == False,  # noqa: E712
            )
            .first()
        )
        # OT is a weekend special when the day falls on the shift's weekly-off list.
        is_weekoff = (
            shift is not None
            and on_date.weekday() in (shift.weekly_off_days or [])
        )
        ot_type = OtType.WEEKEND if is_weekoff else OtType.WEEKDAY
        # Compose a descriptive reason for the auto-detected case.
        post_min_past = 0
        if shift:
            shift_end_dt = _combine(on_date, shift.end_time)
            if result.check_out_time > shift_end_dt:
                post_min_past = int((result.check_out_time - shift_end_dt).total_seconds() / 60)
        auto_reason = (
            f"Auto-detected — clocked out {post_min_past} min past shift end ({round(result.overtime_hours, 2)}h OT)"
            if post_min_past > 0
            else f"Auto-detected — worked {round(result.overtime_hours, 2)}h beyond shift quota"
        )
        if existing_ot:
            # Only update entries still in PENDING — keep approved/processed rows immutable.
            # Don't overwrite an employee-submitted reason with the auto string.
            if existing_ot.status == OtStatus.PENDING:
                existing_ot.ot_hours = round(result.overtime_hours, 2)
                existing_ot.ot_type = ot_type
                # If reason still looks auto-generated (or empty), refresh it
                # with the latest numbers; otherwise leave the employee's text.
                if not existing_ot.reason or existing_ot.reason.startswith("Auto-detected"):
                    existing_ot.reason = auto_reason
        else:
            db.add(OvertimeRequest(
                employee_id=employee_id,
                date=on_date,
                ot_hours=round(result.overtime_hours, 2),
                ot_type=ot_type,
                reason=auto_reason,
                status=OtStatus.PENDING,
                payroll_status=OtPayrollStatus.PENDING,
            ))
        db.flush()

    return att


# ──────────────────────────────────────────────────────────────────────────
# Absentee sweep
# ──────────────────────────────────────────────────────────────────────────

def mark_absentees(db: Session, on_date: date, actor_id: Optional[UUID] = None) -> int:
    """For every ACTIVE employee with no Attendance row for on_date and no
    approved WFH / holiday / weekly off coverage, create ABSENT.
    Idempotent — re-running on the same date is safe.
    """
    from app.models.hr.employee import LifecycleState

    employees = (
        db.query(Employee)
        .filter(Employee.is_deleted == False, Employee.lifecycle_state == LifecycleState.ACTIVE)  # noqa: E712
        .all()
    )
    created = 0
    for emp in employees:
        # Guard: existing row?
        exists = (
            db.query(Attendance.id)
            .filter(Attendance.employee_id == emp.id, Attendance.date == on_date)
            .first()
        )
        if exists:
            continue
        # Apply WFH / holiday / week-off short-circuits via daily_rollup
        att = daily_rollup(db, emp.id, on_date, actor_id=actor_id, source=AttendanceSource.SYSTEM)
        # If still ABSENT, that's what we want; log it
        if att.status == AttendanceStatus.ABSENT:
            log(
                db,
                actor_id=actor_id,
                action=AttendanceLogAction.ABSENTEE_MARKED,
                target_table="hr_attendance",
                target_id=att.id,
                employee_id=emp.id,
                payload={"date": on_date.isoformat()},
            )
            created += 1
    return created


def finalize_orphan_open_punches(
    db: Session,
    employee_id: UUID,
    *,
    lookback_days: int = 7,
    actor_id: Optional[UUID] = None,
) -> int:
    """Auto-close any IN punch from a past IST day that has no matching OUT.

    Triggers when the user lands on the self-service page and we discover a
    stale open IN from yesterday (or earlier — within `lookback_days`). For
    each affected day we:
      1. If there's an open BREAK_START with no BREAK_END, inject a synthetic
         BREAK_END at the same timestamp as the implicit OUT — keeps break
         pair-matching clean for the rollup.
      2. Inject a synthetic OUT punch at the LAST recorded punch time on that
         day. This is the most conservative choice — we only credit time we
         have evidence the user was active. Admin can approve a correction
         to extend it.
      3. Re-run `daily_rollup` so working_hours / break_hours / late / OT all
         get computed against the now-complete IN/OUT pair.
      4. Flag the row + append a remark + log AUTO_CHECKOUT so the next admin
         review sees this was auto-finalized.

    Idempotent — re-running is a no-op (open IN already closed → skipped).
    Returns the number of days that were finalized.
    """
    today = date.today()
    cutoff = today - timedelta(days=lookback_days)
    rows = (
        db.query(Attendance)
        .filter(
            Attendance.employee_id == employee_id,
            Attendance.date >= cutoff,
            Attendance.date < today,
            Attendance.check_in_time.isnot(None),
            Attendance.check_out_time.is_(None),
            Attendance.is_deleted == False,  # noqa: E712
            Attendance.is_locked == False,   # noqa: E712
        )
        .order_by(Attendance.date.asc())
        .all()
    )
    finalized = 0
    for att in rows:
        day_start_utc, day_end_utc = _day_bounds_utc(att.date)
        day_punches = (
            db.query(AttendancePunch)
            .filter(
                AttendancePunch.employee_id == employee_id,
                AttendancePunch.punch_time >= day_start_utc,
                AttendancePunch.punch_time <  day_end_utc,
            )
            .order_by(AttendancePunch.punch_time.asc())
            .all()
        )
        if not day_punches:
            continue
        has_in = any(p.punch_type == PunchType.IN for p in day_punches)
        has_out = any(p.punch_type == PunchType.OUT for p in day_punches)
        if not has_in or has_out:
            continue  # nothing to finalize on this day

        last_punch = day_punches[-1]
        synthetic_time = last_punch.punch_time

        # Close any still-open break first so break-hours math stays correct.
        open_break = False
        for p in day_punches:
            if p.punch_type == PunchType.BREAK_START:
                open_break = True
            elif p.punch_type == PunchType.BREAK_END:
                open_break = False
        if open_break:
            db.add(AttendancePunch(
                employee_id=employee_id,
                punch_time=synthetic_time,
                punch_type=PunchType.BREAK_END,
                source=AttendanceSource.SYSTEM,
                geo_verified=False,
                payload={"auto": True, "reason": "Auto-finalize orphan BREAK_START"},
            ))
            db.flush()

        # Inject the implicit OUT punch.
        out_punch = AttendancePunch(
            employee_id=employee_id,
            punch_time=synthetic_time,
            punch_type=PunchType.OUT,
            source=AttendanceSource.SYSTEM,
            geo_verified=False,
            payload={"auto": True, "reason": "Auto-finalize: user did not clock out before day rolled over"},
        )
        db.add(out_punch)
        db.flush()

        # Re-roll the day so working_hours / status / OT reflect the implicit OUT.
        new_att = daily_rollup(db, employee_id, att.date, actor_id=actor_id, source=AttendanceSource.SYSTEM)
        new_att.is_flagged = True
        prior = (new_att.remarks or "").strip()
        note = f"[AUTO_CHECKOUT] Implicit OUT at {synthetic_time.isoformat()} — user did not clock out before midnight."
        new_att.remarks = (prior + ("\n" if prior else "") + note).strip()

        log(
            db,
            actor_id=actor_id,
            action=AttendanceLogAction.AUTO_CHECKOUT,
            target_table="hr_attendance",
            target_id=new_att.id,
            employee_id=employee_id,
            payload={
                "date": att.date.isoformat(),
                "implicit_out_at": synthetic_time.isoformat(),
                "last_punch_type": last_punch.punch_type.value,
            },
        )
        finalized += 1
    return finalized


def lock_day(db: Session, on_date: date, actor_id: Optional[UUID] = None) -> int:
    rows = (
        db.query(Attendance)
        .filter(Attendance.date == on_date, Attendance.is_locked == False)  # noqa: E712
        .all()
    )
    n = 0
    for r in rows:
        r.is_locked = True
        n += 1
    if n:
        log(
            db,
            actor_id=actor_id,
            action=AttendanceLogAction.DAY_LOCKED,
            target_table="hr_attendance",
            payload={"date": on_date.isoformat(), "count": n},
        )
    return n
