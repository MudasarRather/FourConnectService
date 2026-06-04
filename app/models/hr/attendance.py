"""HR daily Attendance — one row per (employee, date).

Driven by `app.utils.hr.attendance_logic.daily_rollup()` which reads the raw
punches in `hr_attendance_punches` and computes status + working hours per
shift policy. Locked rows (`is_locked=True`) are payroll-frozen; admin PATCH
refuses to edit them unless `?force=true` (in which case it writes an
`AttendanceLog` audit row).
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Text, Index, Numeric, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class AttendanceStatus(str, enum.Enum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"
    HALF_DAY = "HALF_DAY"
    LATE = "LATE"
    LEAVE = "LEAVE"
    WFH = "WFH"
    REMOTE = "REMOTE"
    HOLIDAY = "HOLIDAY"
    WEEK_OFF = "WEEK_OFF"
    ON_DUTY = "ON_DUTY"
    # Authorised unpaid day: a no-show (no clock-in) whose unpaid portion was
    # absorbed by the employee's LWP entitlement. Distinct from ABSENT, which is
    # an *unauthorised* absence (no LWP balance to cover it). Value added live
    # via ALTER TYPE — see add_lwp_attendance_status.py.
    LWP = "LWP"


class AttendanceSource(str, enum.Enum):
    BIOMETRIC = "BIOMETRIC"
    MANUAL = "MANUAL"
    MOBILE = "MOBILE"
    WEB = "WEB"
    KIOSK = "KIOSK"
    SYSTEM = "SYSTEM"


class Attendance(Base):
    """One row per (employee_id, date). Unique constraint enforced."""
    __tablename__ = "hr_attendance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="SET NULL"), nullable=True)

    check_in_time = Column(DateTime(timezone=True), nullable=True)
    check_out_time = Column(DateTime(timezone=True), nullable=True)
    working_hours = Column(Numeric(5, 2), nullable=False, default=0)
    break_hours = Column(Numeric(4, 2), nullable=False, default=0)
    late_minutes = Column(Integer, nullable=False, default=0)
    early_exit_minutes = Column(Integer, nullable=False, default=0)
    overtime_hours = Column(Numeric(5, 2), nullable=False, default=0)

    status = Column(Enum(AttendanceStatus, name="hr_attendance_status"), nullable=False, default=AttendanceStatus.ABSENT, index=True)
    # Intended Loss-of-Pay portion for the (future) payroll module. We only
    # CLASSIFY here — balances are never mutated. 1.0 = full unpaid day
    # (ABSENT), 0.5 = unpaid half-day (short/late day, no half-day request),
    # 0.0 = fully paid/accounted. Payroll consumes this to compute deductions.
    lop_days = Column(Numeric(3, 1), nullable=False, default=0, server_default="0")
    source = Column(Enum(AttendanceSource, name="hr_attendance_source"), nullable=False, default=AttendanceSource.SYSTEM)

    geo_lat = Column(Numeric(10, 7), nullable=True)
    geo_lng = Column(Numeric(10, 7), nullable=True)
    geo_verified = Column(Boolean, nullable=False, default=False)
    device_info = Column(String(255), nullable=True)
    remarks = Column(Text, nullable=True)
    is_flagged = Column(Boolean, nullable=False, default=False)  # outside-geofence or other anomalies
    is_locked = Column(Boolean, nullable=False, default=False, index=True)
    # Admin waiver of a LATE mark — condoned lates don't count toward the
    # monthly late-accumulation penalty (regularisation, not pay-affecting).
    late_condoned = Column(Boolean, nullable=False, default=False, server_default="false")

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # When status==LEAVE (or half-day with one-half leave), the originating
    # LeaveRequest row. Nullable + ON DELETE SET NULL so the Attendance row
    # survives a leave-request soft-delete; daily_rollup re-stamps the link.
    leave_request_id = Column(UUID(as_uuid=True), ForeignKey("hr_leave_requests.id", ondelete="SET NULL"), nullable=True, index=True)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        UniqueConstraint("employee_id", "date", name="uq_hr_attendance_emp_date"),
        Index("ix_hr_attendance_date_status", "date", "status"),
    )
