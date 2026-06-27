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
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from decimal import Decimal
from functools import lru_cache
from typing import List, Optional, Tuple
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.hr.employee import Employee
from app.models.hr.shift import Shift, EmployeeShiftAssignment
from app.models.hr.location import WorkLocation
from app.models.hr.attendance import Attendance, AttendanceStatus, AttendanceSource
from app.models.hr.attendance_punch import AttendancePunch, PunchType
from app.models.hr.wfh_request import WfhRequest, WfhStatus, WfhRequestType
from app.models.hr.holiday import Holiday
from app.models.hr.attendance_log import AttendanceLog, AttendanceLogAction
from app.models.hr.geo_fence import GeoFence


# Default business timezone — the historical org clock and the hard fallback.
# Storage stays UTC; only the *comparison frame* (shift start/end, late minutes,
# half-day cutoff, the day a punch belongs to) moves. India doesn't observe DST
# so a static UTC+5:30 offset is correct and `ZoneInfo("Asia/Kolkata")` is
# arithmetically identical to this fixed offset — so existing all-India data is
# byte-for-byte unaffected by the per-location upgrade below.
IST = timezone(timedelta(hours=5, minutes=30))
DEFAULT_BUSINESS_TZ_NAME = "Asia/Kolkata"


@lru_cache(maxsize=128)
def business_tz(name: Optional[str]) -> tzinfo:
    """Resolve an IANA timezone name to a tzinfo, defaulting to IST.

    Returns the fixed IST offset for an empty/unknown name (and as a hard
    fallback if the IANA db isn't installed) so attendance computation never
    crashes on a mis-typed or unset work-location timezone — the worst case is
    "compute in IST", i.e. the legacy behaviour. ``ZoneInfo`` is DST-correct for
    offices that observe DST (e.g. America/New_York), unlike a fixed offset.
    """
    if not name:
        return IST
    try:
        return ZoneInfo(name)
    except Exception:
        return IST


def resolve_tz_for_location(db: Session, work_location_id: Optional[UUID]) -> tzinfo:
    """Business timezone for a work location id (IST when unset/unknown)."""
    if not work_location_id:
        return IST
    try:
        tz_name = (
            db.query(WorkLocation.timezone)
            .filter(WorkLocation.id == work_location_id)
            .scalar()
        )
        return business_tz(tz_name)
    except Exception:
        return IST


def resolve_employee_tz(db: Session, employee_id: UUID) -> tzinfo:
    """Business timezone an employee operates in, derived from their work
    location's IANA timezone. Falls back to IST when the employee has no
    location, the location has no timezone, or anything goes wrong."""
    try:
        tz_name = (
            db.query(WorkLocation.timezone)
            .join(Employee, Employee.work_location_id == WorkLocation.id)
            .filter(Employee.id == employee_id)
            .scalar()
        )
        return business_tz(tz_name)
    except Exception:
        return IST


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


def _combine(d: date, t: time, tz: tzinfo = IST) -> datetime:
    """Build a tz-aware datetime anchored in the business clock `tz`.

    Shift `start_time` / `end_time` columns are stored as naive wall-clock
    values LOCAL to the office (e.g. 09:00 means 9 AM at that work location),
    so any comparison against a UTC-stored punch timestamp must anchor the shift
    moment in the office timezone. Returning the datetime with `tzinfo=tz` lets
    aware-aware subtraction normalize to UTC correctly and produces the right
    late/early/overtime deltas. `tz` defaults to IST (legacy behaviour).
    """
    return datetime.combine(d, t, tzinfo=tz)


def _day_bounds_utc(d: date, tz: tzinfo = IST) -> tuple[datetime, datetime]:
    """UTC range that covers the office-local calendar day `d`.

    A day in `tz` (00:00–23:59:59 local) spans from `d 00:00 local` to
    `d+1 00:00 local`; converted to UTC this is the window the punch table (keyed
    on UTC timestamps) must be queried with. Using `d 00:00 UTC`..`d+1 00:00 UTC`
    would slice the wrong 24h for any non-UTC office. `tz` defaults to IST so
    existing India data resolves to the historical `d-1 18:30`..`d 18:30 UTC`.
    """
    start_local = datetime.combine(d, time.min, tzinfo=tz)
    end_local = datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


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
    *,
    effective_start: Optional[datetime] = None,
    effective_end: Optional[datetime] = None,
    effective_grace: Optional[int] = None,
    tz: tzinfo = IST,
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

    When the caller has resolved an effective shift window (i.e. a half-day
    is approved — FIRST off shifts start to mid-shift, SECOND off shifts
    end to mid-shift), pass `effective_start` / `effective_end` /
    `effective_grace` so late_minutes / early_exit_minutes are measured
    against the *actual* working half, not the nominal shift.
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
        shift_start_dt = _combine(on_date, shift.start_time, tz)
        shift_end_dt   = _combine(on_date, shift.end_time, tz)
        grace = int(shift.grace_minutes or 0)
        full_day = float(shift.full_day_hours or 8.0)
        half_day = float(shift.half_day_hours or 4.0)

        # If the caller has resolved a half-day window, late / early-exit
        # comparisons run against that — the nominal start/end are still used
        # for OT detection (working past nominal end is still OT).
        late_start_dt = effective_start or shift_start_dt
        early_end_dt  = effective_end or shift_end_dt
        late_grace    = grace if effective_grace is None else int(effective_grace or 0)

        if check_in_time:
            delta_min = (check_in_time - late_start_dt).total_seconds() / 60.0
            late_minutes = max(0, int(delta_min - late_grace))
        if check_out_time and check_out_time < early_end_dt:
            early_exit_minutes = max(0, int((early_end_dt - check_out_time).total_seconds() / 60.0))

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

        # Corporate half-day-on-late cutoff: a clock-in past the EARLIER of the
        # shift mid-point or 2h-after-start, with no half-day request on file,
        # is treated as a HALF_DAY (the employee missed the first half).
        from datetime import timedelta as _td2
        _shift_minutes = (shift_end_dt - shift_start_dt).total_seconds() / 60.0
        if _shift_minutes <= 0:                      # overnight shift
            _shift_minutes += 24 * 60
        half_day_late_cutoff_min = min(_shift_minutes / 2.0, 120.0)
        minutes_after_start = (
            (check_in_time - late_start_dt).total_seconds() / 60.0
            if check_in_time else 0.0
        )

        if not check_in_time:
            status = AttendanceStatus.ABSENT
        elif not check_out_time:
            # Still logged in — don't penalise the partial day as HALF_DAY
            # until the session is actually closed. Late vs Present is still
            # determined by check-in punctuality.
            status = AttendanceStatus.LATE if late_minutes > 0 else AttendanceStatus.PRESENT
        elif working_hours < half_day:
            status = AttendanceStatus.HALF_DAY
        elif minutes_after_start > half_day_late_cutoff_min:
            # Clocked in after the half-day cutoff → HALF_DAY even if hours were
            # later made up, since no half-day was requested in advance.
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
    # Business clock for this employee = their work location's timezone (IST when
    # unset). Every wall-clock anchor below (day bounds, shift start/end, OT,
    # half-day window) is computed in this frame so a non-IST office's punches
    # are judged against its own local shift times.
    tz = resolve_tz_for_location(db, emp.work_location_id if emp else None)
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

    day_start_utc, day_end_utc = _day_bounds_utc(on_date, tz)
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
    from app.models.hr.half_day_request import HalfDayRequest, HalfDayStatus, HalfDayWhich
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

    # Short-circuit: approved Leave covering this date.
    # Full-day leave overrides the punch-derived status to LEAVE. Half-day
    # leave (is_half_day=True) is treated like the existing HalfDayRequest
    # path so a working-half punch still computes hours but the day's status
    # becomes HALF_DAY. The originating leave id is stashed on Attendance
    # for downstream reports/audit.
    from app.models.hr.leave_request import LeaveRequest
    from app.models.hr.leave_type import LeaveStatus as _LeaveStatus
    leave_match = (
        db.query(LeaveRequest)
        .filter(
            LeaveRequest.employee_id == employee_id,
            LeaveRequest.from_date <= on_date,
            LeaveRequest.to_date >= on_date,
            LeaveRequest.status == _LeaveStatus.APPROVED,
            LeaveRequest.is_deleted == False,  # noqa: E712
        )
        .order_by(LeaveRequest.created_at.desc())
        .first()
    )

    # Resolve effective shift window when a half-day is active — so a FIRST-off
    # employee who clocks in at 13:05 doesn't get tagged with ~4h late_minutes,
    # and a SECOND-off employee who leaves at 13:31 doesn't get tagged with
    # ~4.5h early_exit_minutes.
    eff_start = eff_end = None
    eff_grace = None
    if shift and half_day_match:
        s_start = _combine(on_date, shift.start_time, tz)
        s_end   = _combine(on_date, shift.end_time, tz)
        if s_end <= s_start:
            from datetime import timedelta as _td2
            s_end += _td2(days=1)
        s_mid = s_start + (s_end - s_start) / 2
        if half_day_match.which_half == HalfDayWhich.FIRST:
            eff_start, eff_end = s_mid, s_end
        else:
            eff_start, eff_end = s_start, s_mid
        eff_grace = int(getattr(shift, "half_day_grace_minutes", None) or shift.grace_minutes or 0)

    # Compute baseline from punches, then optionally override status
    result = compute_attendance_status(
        punches, shift, on_date,
        effective_start=eff_start, effective_end=eff_end, effective_grace=eff_grace,
        tz=tz,
    )

    if wfh_match:
        # Honour the request discriminator — a REMOTE authorisation must show
        # as REMOTE, not WFH. Both waive the geo-fence; they differ only in
        # label/reporting. (Previously every approved request was hard-coded to
        # WFH, so remote days were mislabelled.)
        result.status = (
            AttendanceStatus.REMOTE
            if wfh_match.request_type == WfhRequestType.REMOTE
            else AttendanceStatus.WFH
        )
        # working_hours is left at the punch-derived value — we do NOT
        # fabricate `full_day_hours` for a punchless WFH/remote day. Crediting
        # a phantom 8.00h "Worked" on a day with zero punches is the same
        # misleading pattern fixed for half-days: the report modal would show
        # hours that were never actually clocked. The day stays WFH/REMOTE
        # (approved, paid — no loss-of-pay); hours are whatever the punches
        # prove (0.00 when the employee didn't punch).
    elif leave_match and not leave_match.is_half_day:
        # Full-day leave wins over half-day / holiday / week-off. If the
        # employee somehow punched in anyway, keep the working_hours for
        # payroll but flag the day as LEAVE.
        result.status = AttendanceStatus.LEAVE
        if not result.working_hours and shift and on_date < date.today():
            result.working_hours = 0.0
    elif half_day_match or (leave_match and leave_match.is_half_day):
        # Approved half-day overrides the punch-based status. working_hours is
        # left at the punch-derived value — we do NOT fabricate
        # `half_day_hours` here. Fabricating it showed "Worked 4.00h" on a day
        # the employee never clocked in (no punches → no working half worked),
        # which is impossible and misleading. The leave-covered half is paid
        # via the leave balance; the working half is credited only by real
        # punches. If the employee did punch in for the working half, their
        # actual hours flow through unchanged.
        result.status = AttendanceStatus.HALF_DAY
    elif holiday_match:
        # Only override if there are no punches (employee may have worked OT)
        if not result.check_in_time:
            result.status = AttendanceStatus.HOLIDAY
    elif is_week_off and not result.check_in_time:
        result.status = AttendanceStatus.WEEK_OFF

    # ── Loss-of-Pay tag (deferred to the payroll phase) ──────────────────
    # We CLASSIFY only and DO NOT touch leave balances here. lop_days records
    # the intended unpaid portion so the future payroll module can compute the
    # deduction:  1.0 = full unpaid day (ABSENT),  0.5 = unpaid half-day (a
    # short/late day with no half-day request),  0.0 = fully accounted/paid
    # (PRESENT/LATE/WFH/LEAVE/HOLIDAY/WEEK_OFF, or an APPROVED half-day which is
    # paid via the leave balance).
    _half_day_is_approved = bool(half_day_match or (leave_match and leave_match.is_half_day))
    if result.status == AttendanceStatus.ABSENT:
        lop_days = 1.0
    elif (
        result.status in (AttendanceStatus.WFH, AttendanceStatus.REMOTE)
        and result.check_in_time is None
    ):
        # Approved WFH/Remote but ZERO punches → full no-show. An off-site
        # authorisation is not, by itself, proof of work — the employee still
        # has to clock in. Routed through LWP coverage like any other no-show.
        lop_days = 1.0
    elif result.status == AttendanceStatus.HALF_DAY and not _half_day_is_approved:
        # Unapproved short/late day — the missing half is unpaid.
        lop_days = 0.5
    elif (
        result.status == AttendanceStatus.HALF_DAY
        and _half_day_is_approved
        and result.check_in_time is None
    ):
        # Approved half-day, but the employee never clocked in for the working
        # half — that half is unworked → loss-of-pay. (The leave-covered half
        # is paid via the leave balance, so this is 0.5, not 1.0.)
        lop_days = 0.5
    else:
        lop_days = 0.0

    # ── LWP coverage / ABSENT downgrade for elapsed no-show days ─────────
    # A genuine no-show (no clock-in) on an elapsed working day is run through
    # the employee's LWP entitlement: if LWP can absorb it we debit LWP (a full
    # no-show becomes status LWP; an approved half-day stays HALF_DAY), else the
    # day is an unauthorised ABSENT. A day the employee actually WORKED keeps its
    # payroll lop_days but never consumes LWP. The helper also releases any
    # stale auto-LWP debit when a day later gains punches — so it runs for every
    # elapsed working day (it no-ops cheaply when there's nothing to do).
    # Skipped for today/future (status still settling — see recompute caution)
    # and for holiday/week-off (no unpaid portion).
    if on_date < date.today() and not holiday_match and not is_week_off:
        _is_no_show = result.check_in_time is None
        if lop_days > 0 or _is_no_show:
            from app.utils.hr.lwp_coverage import apply_lwp_coverage
            _lwp = apply_lwp_coverage(
                db, employee_id, on_date,
                status=result.status, lop_days=lop_days,
                is_no_show=_is_no_show, actor_id=actor_id,
            )
            result.status = _lwp.status
            lop_days = _lwp.lop_days

        # Monthly late-mark accumulation: every Nth non-condoned LATE in the
        # month costs a fraction of a day, debited from LWP (drawn from whatever
        # LWP remains after no-show coverage above). Reconciled per rollup so
        # condoning / correcting a late releases the penalty. Worked LATE days
        # keep their status — this is a pay penalty, not an absence.
        from app.utils.hr.late_penalty import reconcile_late_penalty
        reconcile_late_penalty(db, employee_id, on_date, actor_id=actor_id)

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

    leave_request_id_val = leave_match.id if leave_match else None

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
        existing.lop_days = lop_days
        # Re-stamp the originating leave on every rollup so admin
        # delete/restore of a LeaveRequest flips the row cleanly.
        existing.leave_request_id = leave_request_id_val
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
            lop_days=lop_days,
            source=source,
            geo_lat=geo_lat,
            geo_lng=geo_lng,
            geo_verified=geo_verified,
            leave_request_id=leave_request_id_val,
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
    # Never queue OT on an approved-LEAVE day: the employee booked the day off,
    # so any punches there are not OT. To legitimately work (and earn OT) on a
    # booked day the leave must first be cancelled — which flips the day off LEAVE
    # and lets the next rollup queue the OT. (Payroll also refuses to pay OT on a
    # LEAVE day as a backstop — see _OT_NONWORK_STATUSES.)
    if (
        result.overtime_hours
        and result.overtime_hours > 0
        and result.check_out_time is not None
        and result.status != AttendanceStatus.LEAVE
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
            shift_end_dt = _combine(on_date, shift.end_time, tz)
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

    # ──────────────────────────────────────────────────────────────────
    # Auto-credit COMP_OFF when an employee worked through a Holiday or
    # Week-Off for at least half a shift. Idempotent: a marker row in
    # `hr_leave_balance_history` (kind=COMP_OFF_EARNED, earned_on=on_date)
    # prevents double-credit. Manual admin grants land via the dedicated
    # endpoint and never collide with this auto-detect because they don't
    # share an `is_auto_generated=True` row for the same date.
    # ──────────────────────────────────────────────────────────────────
    if (
        shift
        and result.check_in_time is not None
        and result.check_out_time is not None
        and (holiday_match or is_week_off)
        and float(result.working_hours or 0) >= float(shift.half_day_hours or 4.0) / 2.0
    ):
        from app.models.hr.leave_balance_history import LeaveBalanceHistory
        from app.models.hr.leave_balance import LeaveBalance
        from app.models.hr.leave_type import LeaveType, LedgerKind
        from app.models.system_setting import SystemSetting
        from decimal import Decimal as _Decimal
        # Already credited?
        marker = (
            db.query(LeaveBalanceHistory.id)
            .filter(
                LeaveBalanceHistory.employee_id == employee_id,
                LeaveBalanceHistory.leave_type == LeaveType.COMP_OFF,
                LeaveBalanceHistory.kind == LedgerKind.COMP_OFF_EARNED,
                LeaveBalanceHistory.earned_on == on_date,
            )
            .first()
        )
        # No double-dip: if this HOLIDAY is cash-compensated via a holiday-shift
        # assignment (DOUBLE_PAY / OVERTIME → paid as double wages / OT in payroll),
        # do NOT also grant a comp-off leave day. Comp-off still applies to COMP_OFF
        # assignments, holiday-allowance, unassigned holiday work, and all week-off
        # work. (Indian N&FH / Factories Act: double wages OR a compensatory holiday.)
        cash_in_lieu = False
        if holiday_match:
            from app.models.hr.holiday_shift import HolidayShiftAssignment, HolidayCompType
            _hsa = (
                db.query(HolidayShiftAssignment.compensation)
                .filter(HolidayShiftAssignment.employee_id == employee_id,
                        HolidayShiftAssignment.holiday_id == holiday_match.id,
                        HolidayShiftAssignment.is_deleted == False)  # noqa: E712
                .first()
            )
            if _hsa and _hsa[0] in (HolidayCompType.DOUBLE_PAY, HolidayCompType.OVERTIME):
                cash_in_lieu = True
        if not marker and not cash_in_lieu:
            # Decide credit size: full day if worked ≥ full_day_hours, else 0.5
            full_h = float(shift.full_day_hours or 8.0)
            worked_h = float(result.working_hours or 0)
            credit = _Decimal("1.0") if worked_h >= full_h else _Decimal("0.5")
            # Resolve current fiscal year (without circular-importing leaves router).
            fy_setting = db.query(SystemSetting).filter(SystemSetting.key == "fiscal_year_start").first()
            fy_start = fy_setting.value if fy_setting else "04-01"
            try:
                mm, dd = (int(x) for x in fy_start.split("-"))
            except Exception:
                mm, dd = 4, 1
            boundary = date(on_date.year, mm, dd)
            sy = on_date.year if on_date >= boundary else on_date.year - 1
            fy = f"{sy}-{str(sy + 1)[-2:]}"
            # Expiry window
            exp_setting = db.query(SystemSetting).filter(SystemSetting.key == "comp_off_expiry_days").first()
            try:
                exp_days = int(exp_setting.value) if exp_setting else 90
            except Exception:
                exp_days = 90
            expires = on_date + timedelta(days=exp_days)
            # Get/create balance row
            bal = (
                db.query(LeaveBalance)
                .filter(
                    LeaveBalance.employee_id == employee_id,
                    LeaveBalance.leave_type == LeaveType.COMP_OFF,
                    LeaveBalance.fiscal_year == fy,
                    LeaveBalance.is_deleted == False,  # noqa: E712
                )
                .first()
            )
            if not bal:
                bal = LeaveBalance(
                    employee_id=employee_id, leave_type=LeaveType.COMP_OFF, fiscal_year=fy,
                    opening_balance=0, accrued=0, carry_forward_in=0,
                    used=0, encashed=0, adjustments=0, closing_balance=0,
                )
                db.add(bal); db.flush()
            before = (_Decimal(bal.opening_balance or 0) + _Decimal(bal.accrued or 0)
                      + _Decimal(bal.carry_forward_in or 0) + _Decimal(bal.adjustments or 0)
                      - _Decimal(bal.used or 0) - _Decimal(bal.encashed or 0))
            bal.adjustments = _Decimal(bal.adjustments or 0) + credit
            after = before + credit
            bal.closing_balance = after
            db.add(LeaveBalanceHistory(
                employee_id=employee_id,
                leave_type=LeaveType.COMP_OFF,
                fiscal_year=fy,
                kind=LedgerKind.COMP_OFF_EARNED,
                delta=credit, balance_before=before, balance_after=after,
                actor_user_id=actor_id,
                note=("Auto-credited for working on " +
                      ("holiday " + (holiday_match.name if holiday_match else "")
                       if holiday_match else "week-off")),
                is_auto_generated=True,
                earned_on=on_date,
                expires_on=expires,
            ))
            try:
                log(db, actor_id=actor_id, action=AttendanceLogAction.COMP_OFF_EARNED,
                    target_table="hr_leave_balances", target_id=bal.id, employee_id=employee_id,
                    payload={"earned_on": on_date.isoformat(), "days": float(credit),
                             "expires_on": expires.isoformat(),
                             "reason": "holiday" if holiday_match else "week_off"})
            except Exception:
                pass
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
    tz = resolve_employee_tz(db, employee_id)
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
        day_start_utc, day_end_utc = _day_bounds_utc(att.date, tz)
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
