"""Pydantic schemas for the HR Attendance module.

One file covers every resource (Shift, Attendance, Punch, Correction, WFH,
Overtime, Holiday, Policy, GeoFence, BiometricDevice, AttendanceLog) plus the
dashboard / heatmap / me-today DTOs.
"""
from __future__ import annotations

from datetime import date, datetime, time
# Alias to dodge a `from __future__ import annotations` gotcha: a PATCH-style
# schema with `date: Optional[date] = None` binds `date` to None in the class
# namespace, which then shadows the imported type when Pydantic resolves the
# annotation. Update-classes use `_DateType` instead — see HolidayUpdate.
_DateType = date
from decimal import Decimal
from typing import List, Optional, Any, Dict
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.hr.shift import ShiftType
from app.models.hr.attendance import AttendanceStatus, AttendanceSource
from app.models.hr.attendance_punch import PunchType
from app.models.hr.attendance_correction import CorrectionStatus
from app.models.hr.wfh_request import WfhStatus, WfhRequestType
from app.models.hr.half_day_request import HalfDayStatus, HalfDayWhich, HalfDayReason
from app.models.hr.overtime import OtType, OtStatus, OtPayrollStatus
from app.models.hr.holiday import HolidayType
from app.models.hr.attendance_policy import PolicyType
from app.models.hr.biometric_device import BiometricDeviceType, BiometricDeviceStatus
from app.models.hr.attendance_log import AttendanceLogAction


# ═══════════════════════════════════════════════════════════════════════════
# SHIFT
# ═══════════════════════════════════════════════════════════════════════════

class BreakWindow(BaseModel):
    """One configured break slot on a shift."""
    label: str = Field(..., max_length=40)        # e.g. "Lunch", "Tea"
    start_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")  # "13:00"
    end_time: str = Field(..., pattern=r"^\d{2}:\d{2}$")    # "14:00"
    max_minutes: int = Field(..., ge=1, le=240)


class ShiftBase(BaseModel):
    code: str = Field(..., max_length=40)
    name: str = Field(..., max_length=120)
    shift_type: ShiftType = ShiftType.GENERAL
    start_time: time
    end_time: time
    break_minutes: int = 60
    grace_minutes: int = 10
    weekly_off_days: List[int] = Field(default_factory=lambda: [5, 6])
    half_day_hours: float = 4.0
    full_day_hours: float = 8.0
    night_allowance: bool = False
    description: Optional[str] = None
    break_windows: List[BreakWindow] = Field(default_factory=list)
    late_punch_requires_approval: bool = True
    late_self_punch_threshold_minutes: int = 15
    break_overrun_alert_minutes: int = 10


class ShiftCreate(ShiftBase):
    pass


class ShiftUpdate(BaseModel):
    name: Optional[str] = None
    shift_type: Optional[ShiftType] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    break_minutes: Optional[int] = None
    grace_minutes: Optional[int] = None
    weekly_off_days: Optional[List[int]] = None
    half_day_hours: Optional[float] = None
    full_day_hours: Optional[float] = None
    night_allowance: Optional[bool] = None
    is_active: Optional[bool] = None
    description: Optional[str] = None
    break_windows: Optional[List[BreakWindow]] = None
    late_punch_requires_approval: Optional[bool] = None
    late_self_punch_threshold_minutes: Optional[int] = None
    break_overrun_alert_minutes: Optional[int] = None


class ShiftResponse(ShiftBase):
    id: UUID
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShiftListResponse(BaseModel):
    items: List[ShiftResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ── Employee shift assignment ──

class EmployeeShiftAssignmentCreate(BaseModel):
    employee_id: UUID
    shift_id: UUID
    effective_from: date
    effective_until: Optional[date] = None
    is_default: bool = False
    notes: Optional[str] = None


class EmployeeShiftAssignmentResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    shift_id: UUID
    shift_code: Optional[str] = None
    shift_name: Optional[str] = None
    effective_from: date
    effective_until: Optional[date]
    is_default: bool
    notes: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ShiftAssignBulkBody(BaseModel):
    employee_ids: List[UUID]
    effective_from: date
    effective_until: Optional[date] = None
    is_default: bool = True
    notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# ATTENDANCE
# ═══════════════════════════════════════════════════════════════════════════

class AttendanceResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department: Optional[str] = None
    designation: Optional[str] = None
    date: date
    shift_id: Optional[UUID] = None
    shift_name: Optional[str] = None
    check_in_time: Optional[datetime]
    check_out_time: Optional[datetime]
    working_hours: float
    break_hours: float
    late_minutes: int
    early_exit_minutes: int
    overtime_hours: float
    status: AttendanceStatus
    source: AttendanceSource
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    geo_verified: bool
    device_info: Optional[str]
    remarks: Optional[str]
    is_flagged: bool
    is_locked: bool
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AttendanceListResponse(BaseModel):
    items: List[AttendanceResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class AttendanceUpdate(BaseModel):
    """Admin manual edit. Refused on locked rows without ?force=true."""
    check_in_time: Optional[datetime] = None
    check_out_time: Optional[datetime] = None
    status: Optional[AttendanceStatus] = None
    remarks: Optional[str] = None


class AttendanceCreateManual(BaseModel):
    """Admin manual creation (eg. ON_DUTY)."""
    employee_id: UUID
    date: date
    status: AttendanceStatus
    check_in_time: Optional[datetime] = None
    check_out_time: Optional[datetime] = None
    remarks: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# PUNCH
# ═══════════════════════════════════════════════════════════════════════════

class PunchCreate(BaseModel):
    punch_type: PunchType = PunchType.IN
    source: AttendanceSource = AttendanceSource.WEB
    geo_lat: Optional[float] = None
    geo_lng: Optional[float] = None
    device_info: Optional[str] = None
    selfie_url: Optional[str] = None
    justification: Optional[str] = None  # required when outside geofence


class PunchResponse(BaseModel):
    id: UUID
    employee_id: UUID
    attendance_id: Optional[UUID]
    punch_time: datetime
    punch_type: PunchType
    source: AttendanceSource
    device_id: Optional[str]
    geo_lat: Optional[float]
    geo_lng: Optional[float]
    geo_verified: bool
    geo_distance_m: Optional[float]
    selfie_url: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PunchListResponse(BaseModel):
    items: List[PunchResponse]


# ═══════════════════════════════════════════════════════════════════════════
# CORRECTION
# ═══════════════════════════════════════════════════════════════════════════

class CorrectionCreate(BaseModel):
    attendance_date: date
    requested_check_in: Optional[datetime] = None
    requested_check_out: Optional[datetime] = None
    reason: str = Field(..., min_length=4)
    attachment_url: Optional[str] = None


class CorrectionDecideBody(BaseModel):
    decision: CorrectionStatus  # APPROVED or REJECTED
    level: str = Field(default="HR", description="MANAGER or HR")
    notes: Optional[str] = None
    force: bool = False  # force-edit a locked Attendance row


class CorrectionResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    attendance_id: Optional[UUID]
    attendance_date: date
    original_check_in: Optional[datetime]
    original_check_out: Optional[datetime]
    requested_check_in: Optional[datetime]
    requested_check_out: Optional[datetime]
    reason: str
    attachment_url: Optional[str]
    status: CorrectionStatus
    manager_approved_by_id: Optional[UUID]
    manager_approved_at: Optional[datetime]
    hr_approved_by_id: Optional[UUID]
    hr_approved_at: Optional[datetime]
    decision_notes: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class CorrectionListResponse(BaseModel):
    items: List[CorrectionResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═══════════════════════════════════════════════════════════════════════════
# WFH
# ═══════════════════════════════════════════════════════════════════════════

class WfhCreate(BaseModel):
    request_type: WfhRequestType = WfhRequestType.WFH
    wfh_date: date
    wfh_date_until: Optional[date] = None
    reason: str = Field(..., min_length=4)


class WfhUpdate(BaseModel):
    work_summary: Optional[str] = None


class WfhDecideBody(BaseModel):
    decision: WfhStatus  # APPROVED or REJECTED
    notes: Optional[str] = None


class WfhResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    request_type: WfhRequestType
    wfh_date: date
    wfh_date_until: Optional[date]
    reason: str
    work_summary: Optional[str]
    status: WfhStatus
    manager_approved_by_id: Optional[UUID]
    manager_approved_at: Optional[datetime]
    decision_notes: Optional[str]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class WfhListResponse(BaseModel):
    items: List[WfhResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═══════════════════════════════════════════════════════════════════════════
# HALF-DAY REQUESTS
# ═══════════════════════════════════════════════════════════════════════════

class HalfDayCreate(BaseModel):
    """Employee-initiated half-day request."""
    half_day_date: _DateType
    which_half: HalfDayWhich = HalfDayWhich.SECOND
    reason_type: HalfDayReason = HalfDayReason.PERSONAL
    reason: str = Field(..., min_length=4, max_length=500)


class HalfDayAdminCreate(BaseModel):
    """Admin manual tag — pre-approved, no employee submission needed."""
    employee_id: UUID
    half_day_date: _DateType
    which_half: HalfDayWhich = HalfDayWhich.SECOND
    reason_type: HalfDayReason = HalfDayReason.OFFICIAL
    reason: str = Field(..., min_length=4, max_length=500)


class HalfDayDecideBody(BaseModel):
    """Approve / reject payload — `decision` field is informational; the
    chosen endpoint (``/approve`` vs ``/reject``) carries the real intent."""
    decision: HalfDayStatus
    notes: Optional[str] = None


class HalfDayResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department: Optional[str] = None
    half_day_date: date
    which_half: HalfDayWhich
    reason_type: HalfDayReason
    reason: str
    status: HalfDayStatus
    manager_approved_by_id: Optional[UUID]
    manager_approved_by_name: Optional[str] = None
    manager_approved_at: Optional[datetime]
    decision_notes: Optional[str]
    is_admin_override: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class HalfDayListResponse(BaseModel):
    items: List[HalfDayResponse]
    total: int
    page: int
    limit: int
    total_pages: int


class HalfDayStats(BaseModel):
    """Lightweight tile-row stats for the admin dashboard banner."""
    pending: int
    approved: int
    rejected: int
    upcoming: int   # APPROVED with half_day_date >= today


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD / HEATMAP / ME
# ═══════════════════════════════════════════════════════════════════════════

class AttendanceDashboardStats(BaseModel):
    headcount: int
    present_today: int
    absent_today: int
    on_leave: int
    on_wfh: int
    late_count: int
    pending_late_count: int = 0  # late-punch requests waiting on approval
    on_time_pct: float
    overtime_count: int
    pending_corrections: int
    pending_wfh: int
    biometric_errors: int


class DeptAttendance(BaseModel):
    department: str
    present: int
    absent: int
    on_leave: int


class DashboardByDeptResponse(BaseModel):
    items: List[DeptAttendance]


class HeatmapCell(BaseModel):
    day: int   # 0=Mon .. 6=Sun
    hour: int  # 0..23
    density: float  # 0..1
    present: int = 0
    late: int = 0
    absent: int = 0


class HeatmapResponse(BaseModel):
    range_start: date
    range_end: date
    cells: List[HeatmapCell]


class CurrentBreakWindow(BaseModel):
    label: str
    start_time: str
    end_time: str
    max_minutes: int
    is_active_now: bool
    minutes_until_start: Optional[int] = None  # if not active yet
    minutes_until_end: Optional[int] = None    # if active now


class MeTodayResponse(BaseModel):
    """Bundle the self-service page hydrates from on mount."""
    employee_id: UUID
    today: date
    shift: Optional[ShiftResponse] = None
    attendance: Optional[AttendanceResponse] = None
    open_punch: Optional[PunchType] = None  # last open IN/BREAK_START, if any
    elapsed_seconds: int = 0
    can_clock_in: bool = True
    can_clock_out: bool = False
    can_break_start: bool = False
    can_break_end: bool = False
    is_holiday: bool = False
    holiday_name: Optional[str] = None
    is_week_off: bool = False
    wfh_approved: bool = False
    next_action: str = "clock_in"  # clock_in | break_start | break_end | clock_out | done

    # Policy state — drives the frontend UX
    is_late: bool = False                    # past shift.start + grace
    late_minutes_now: int = 0                # if employee clocked in right now
    requires_late_approval: bool = False     # past start+grace+threshold AND policy on
    pending_late_request_id: Optional[UUID] = None
    pending_late_request_status: Optional[str] = None  # PENDING | APPROVED | REJECTED

    # Early clock-in — frontend disables the button when the user is too early.
    is_too_early_to_punch: bool = False
    minutes_until_clock_in_opens: int = 0
    clock_in_opens_at: Optional[str] = None        # "HH:MM" — first allowed clock-in moment
    minutes_until_shift_start: int = 0

    # Early clock-out — frontend routes to the early-exit modal instead of OUT.
    requires_early_exit_approval: bool = False
    minutes_until_shift_end: int = 0
    pending_early_exit_request_id: Optional[UUID] = None
    pending_early_exit_request_status: Optional[str] = None
    has_approved_early_exit: bool = False

    break_used_minutes: int = 0              # total break minutes consumed today
    break_remaining_minutes: int = 0         # max - used (clamped >=0)
    current_break_window: Optional[CurrentBreakWindow] = None
    next_break_window: Optional[CurrentBreakWindow] = None
    in_break_window_now: bool = True         # if shift has no windows, True


class LatePunchRequestCreate(BaseModel):
    reason: str = Field(..., min_length=4, max_length=500)


class LatePunchRequestResponse(BaseModel):
    correction_id: UUID
    attendance_date: date
    requested_check_in: datetime
    reason: str
    status: str
    minutes_late: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class EarlyExitRequestCreate(BaseModel):
    reason: str = Field(..., min_length=4, max_length=500)


class EarlyExitRequestResponse(BaseModel):
    correction_id: UUID
    attendance_date: date
    reason: str
    status: str
    minutes_remaining: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class MyHistoryDay(BaseModel):
    date: date
    status: AttendanceStatus
    working_hours: float
    check_in_time: Optional[datetime]
    check_out_time: Optional[datetime]


class MyHistoryResponse(BaseModel):
    items: List[MyHistoryDay]


class MyMonthCell(BaseModel):
    date: date
    status: AttendanceStatus
    working_hours: float


class MyMonthResponse(BaseModel):
    year: int
    month: int
    cells: List[MyMonthCell]


# Per-day detail returned to the self-service Attendance Report.
class MyDayPunch(BaseModel):
    id: UUID
    punch_time: datetime
    punch_type: PunchType
    geo_verified: bool
    source: AttendanceSource
    is_auto: bool = False  # synthesised by AUTO_CHECKOUT finalizer


class MyDayBreakSegment(BaseModel):
    start: datetime
    end: Optional[datetime]
    minutes: float
    is_open: bool = False


class MyDayDetailResponse(BaseModel):
    date: date
    status: AttendanceStatus
    shift: Optional[ShiftResponse] = None
    check_in_time: Optional[datetime] = None
    check_out_time: Optional[datetime] = None
    working_hours: float = 0.0
    break_hours: float = 0.0
    break_count: int = 0
    late_minutes: int = 0
    early_exit_minutes: int = 0
    overtime_hours: float = 0.0
    is_flagged: bool = False
    is_locked: bool = False
    is_auto_closed: bool = False    # True when AUTO_CHECKOUT log exists for this row
    remarks: Optional[str] = None
    punches: List[MyDayPunch] = Field(default_factory=list)
    breaks: List[MyDayBreakSegment] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# OVERTIME (skeleton)
# ═══════════════════════════════════════════════════════════════════════════

class OvertimeCreate(BaseModel):
    employee_id: UUID
    date: date
    ot_hours: float
    ot_type: OtType = OtType.WEEKDAY
    reason: Optional[str] = None


class OvertimeDecideBody(BaseModel):
    decision: OtStatus
    notes: Optional[str] = None


class OvertimeResponse(BaseModel):
    id: UUID
    employee_id: UUID
    employee_name: Optional[str] = None
    date: date
    ot_hours: float
    ot_type: OtType
    reason: Optional[str]
    status: OtStatus
    payroll_status: OtPayrollStatus
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OvertimeListResponse(BaseModel):
    items: List[OvertimeResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# Self-service: employee submits an OT request for themselves. Omits
# employee_id (resolved server-side from the JWT). Hours capped to a sane
# daily window so a single bad request can't ask for 24h of OT.
class MyOvertimeCreate(BaseModel):
    date: date
    ot_hours: float = Field(..., gt=0, le=12)
    ot_type: OtType = OtType.WEEKDAY
    reason: str = Field(..., min_length=4, max_length=500)


# ═══════════════════════════════════════════════════════════════════════════
# HOLIDAY (skeleton)
# ═══════════════════════════════════════════════════════════════════════════

class HolidayCreate(BaseModel):
    name: str
    date: date
    holiday_type: HolidayType = HolidayType.COMPANY
    location_id: Optional[UUID] = None
    description: Optional[str] = None
    # Admin-created holidays default to ACTIVE; imported drafts override this.
    is_active: bool = True


class HolidayUpdate(BaseModel):
    """Admin-side edit on a single holiday row. All fields optional —
    only the keys actually sent are applied (PATCH semantics).

    Uses `_DateType` instead of `date` because the `date = None` default
    would otherwise shadow the imported `date` type under
    `from __future__ import annotations`, causing the field to be resolved
    as `Optional[None]` and rejecting every non-null value with 422.
    """
    name: Optional[str] = None
    date: Optional[_DateType] = None
    holiday_type: Optional[HolidayType] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    location_id: Optional[UUID] = None


class HolidayResponse(BaseModel):
    id: UUID
    name: str
    date: date
    holiday_type: HolidayType
    location_id: Optional[UUID]
    description: Optional[str]
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class HolidayListResponse(BaseModel):
    items: List[HolidayResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═══════════════════════════════════════════════════════════════════════════
# POLICY (skeleton)
# ═══════════════════════════════════════════════════════════════════════════

class AttendancePolicyCreate(BaseModel):
    name: str
    policy_type: PolicyType
    description: Optional[str] = None
    rules: Dict[str, Any] = Field(default_factory=dict)
    applicable_department_ids: List[UUID] = Field(default_factory=list)
    applicable_shift_ids: List[UUID] = Field(default_factory=list)
    effective_from: Optional[date] = None
    effective_until: Optional[date] = None


class AttendancePolicyResponse(BaseModel):
    id: UUID
    name: str
    policy_type: PolicyType
    description: Optional[str]
    rules: Dict[str, Any]
    applicable_department_ids: List[UUID]
    applicable_shift_ids: List[UUID]
    effective_from: Optional[date]
    effective_until: Optional[date]
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AttendancePolicyListResponse(BaseModel):
    items: List[AttendancePolicyResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═══════════════════════════════════════════════════════════════════════════
# GEO FENCE (skeleton)
# ═══════════════════════════════════════════════════════════════════════════

class GeoFenceCreate(BaseModel):
    name: str
    location_id: Optional[UUID] = None
    center_lat: float
    center_lng: float
    radius_meters: int = 200


class GeoFenceResponse(BaseModel):
    id: UUID
    name: str
    location_id: Optional[UUID]
    center_lat: float
    center_lng: float
    radius_meters: int
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class GeoFenceListResponse(BaseModel):
    items: List[GeoFenceResponse]


# ═══════════════════════════════════════════════════════════════════════════
# BIOMETRIC DEVICE (skeleton)
# ═══════════════════════════════════════════════════════════════════════════

class BiometricDeviceCreate(BaseModel):
    device_id: str
    name: str
    device_type: BiometricDeviceType = BiometricDeviceType.ZKTECO
    location_id: Optional[UUID] = None
    ip_address: Optional[str] = None


class BiometricDeviceResponse(BaseModel):
    id: UUID
    device_id: str
    name: str
    device_type: BiometricDeviceType
    location_id: Optional[UUID]
    ip_address: Optional[str]
    last_sync_at: Optional[datetime]
    last_sync_status: BiometricDeviceStatus
    last_sync_message: Optional[str]
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class BiometricDeviceListResponse(BaseModel):
    items: List[BiometricDeviceResponse]


# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOG (read-only)
# ═══════════════════════════════════════════════════════════════════════════

class AttendanceLogResponse(BaseModel):
    id: UUID
    actor_user_id: Optional[UUID]
    actor_name: Optional[str] = None
    action: AttendanceLogAction
    target_table: Optional[str]
    target_id: Optional[UUID]
    employee_id: Optional[UUID]
    payload: Dict[str, Any]
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class AttendanceLogListResponse(BaseModel):
    items: List[AttendanceLogResponse]
    total: int
    page: int
    limit: int
    total_pages: int


# ═══════════════════════════════════════════════════════════════════════════
# BREAK ANOMALY — admin-facing report of employees who exceeded shift break cap
# ═══════════════════════════════════════════════════════════════════════════

class BreakAnomalySegment(BaseModel):
    """One break segment (start → end with duration in minutes)."""
    start: datetime
    end: Optional[datetime] = None
    minutes: float
    is_open: bool = False  # True when end is null (still on break)
    is_over_window: bool = False  # break landed outside any configured break_window


class BreakAnomalyRow(BaseModel):
    """One employee's break anomaly for a given date."""
    employee_id: UUID
    employee_name: Optional[str] = None
    employee_code: Optional[str] = None
    department_name: Optional[str] = None
    date: date
    shift_name: Optional[str] = None
    # Caps and actuals (everything in minutes; frontend converts as needed).
    break_cap_minutes: int
    break_actual_minutes: int
    excess_minutes: int
    overage_ratio: float  # break_actual / break_cap (e.g. 1.32 = 32% over)
    severity: str  # MILD | SEVERE | CRITICAL | WITHIN_CAP
    break_count: int
    segments: List[BreakAnomalySegment] = Field(default_factory=list)
    status: AttendanceStatus
    is_flagged: bool = False
    has_open_break: bool = False  # True when at least one segment is still open


class BreakAnomalySummary(BaseModel):
    """Headline stats for the break-anomaly section."""
    on_date: date
    total_flagged: int
    mild_count: int          # 1.0×–1.5× of cap
    severe_count: int        # 1.5×–2.0×
    critical_count: int      # >2.0×
    within_cap_count: int = 0  # break_hours > 0 but ≤ cap (only when include_within_cap=true)
    open_break_count: int = 0  # employees with at least one open BREAK_START today
    total_excess_minutes: int  # sum across all flagged rows
    avg_overage_ratio: float


class BreakAnomalyListResponse(BaseModel):
    summary: BreakAnomalySummary
    items: List[BreakAnomalyRow]
    total: int
    page: int
    limit: int
    total_pages: int
