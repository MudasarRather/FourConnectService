"""HR Shift templates + per-employee shift assignments.

A `Shift` is a reusable schedule template (Morning 9-6, Night 10-7, etc.).
An `EmployeeShiftAssignment` ties an employee to a shift for an effective-dated
period; assigning a new shift closes the previous active assignment by setting
its `effective_until = new.effective_from - 1`.

`Employee.shift_id` is a denormalised "current default" column (already exists
on the Employee model as an unbound UUID); the canonical source-of-truth is
`EmployeeShiftAssignment`. Helper resolver lives in
`app.utils.hr.attendance_logic.resolve_shift`.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date, Time,
    Enum, Integer, Index, Numeric, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class ShiftType(str, enum.Enum):
    GENERAL = "GENERAL"
    NIGHT = "NIGHT"
    ROTATIONAL = "ROTATIONAL"
    FLEXIBLE = "FLEXIBLE"


class Shift(Base):
    """Reusable shift template (start / end / break / grace / weekly-off)."""
    __tablename__ = "hr_shifts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    code = Column(String(40), unique=True, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    shift_type = Column(Enum(ShiftType, name="hr_shift_type"), nullable=False, default=ShiftType.GENERAL)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    break_minutes = Column(Integer, nullable=False, default=60)
    grace_minutes = Column(Integer, nullable=False, default=10)
    # 0=Mon .. 6=Sun (Python weekday()). Default = Saturday + Sunday off.
    weekly_off_days = Column(JSONB, nullable=False, default=lambda: [5, 6])
    half_day_hours = Column(Numeric(4, 2), nullable=False, default=4.0)
    full_day_hours = Column(Numeric(4, 2), nullable=False, default=8.0)
    night_allowance = Column(Boolean, nullable=False, default=False)
    # Break windows: list of allowed break slots, e.g.
    #   [{"label":"Lunch","start_time":"13:00","end_time":"14:00","max_minutes":30},
    #    {"label":"Tea","start_time":"16:00","end_time":"16:15","max_minutes":15}]
    # If empty, no time restriction — only total break_minutes is enforced.
    break_windows = Column(JSONB, nullable=False, default=list)
    # Punch-in policy: when True, if employee tries to clock in more than
    # `late_self_punch_threshold_minutes` after shift start, the punch is
    # blocked and routed through an AttendanceCorrection for admin approval.
    late_punch_requires_approval = Column(Boolean, nullable=False, default=True)
    late_self_punch_threshold_minutes = Column(Integer, nullable=False, default=15)
    # Alert config: minutes past expected break-end before admin is notified.
    break_overrun_alert_minutes = Column(Integer, nullable=False, default=10)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    description = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class EmployeeShiftAssignment(Base):
    """Effective-dated shift assignment per employee."""
    __tablename__ = "hr_employee_shift_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="RESTRICT"), nullable=False, index=True)
    effective_from = Column(Date, nullable=False, index=True)
    effective_until = Column(Date, nullable=True)
    is_default = Column(Boolean, nullable=False, default=False)
    notes = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    shift = relationship("Shift", foreign_keys=[shift_id])
    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_emp_shift_emp_from", "employee_id", "effective_from"),
    )
