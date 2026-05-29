"""Attendance audit log — append-only.

Every admin-driven mutation (manual edit, correction approval, lock-day,
absentee mark, policy change, shift assignment, biometric sync) writes one
row here so we have an immutable audit trail. Router exposes GET only.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, DateTime, ForeignKey,
    Enum, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database import Base


class AttendanceLogAction(str, enum.Enum):
    PUNCH = "PUNCH"
    MANUAL_EDIT = "MANUAL_EDIT"
    CORRECTION_REQUESTED = "CORRECTION_REQUESTED"
    CORRECTION_APPROVED = "CORRECTION_APPROVED"
    CORRECTION_REJECTED = "CORRECTION_REJECTED"
    BIOMETRIC_SYNC = "BIOMETRIC_SYNC"
    POLICY_CHANGE = "POLICY_CHANGE"
    SHIFT_ASSIGNED = "SHIFT_ASSIGNED"
    WFH_APPROVED = "WFH_APPROVED"
    WFH_REJECTED = "WFH_REJECTED"
    HALF_DAY_REQUESTED = "HALF_DAY_REQUESTED"
    HALF_DAY_APPROVED = "HALF_DAY_APPROVED"
    HALF_DAY_REJECTED = "HALF_DAY_REJECTED"
    HALF_DAY_OVERRIDE = "HALF_DAY_OVERRIDE"
    OT_APPROVED = "OT_APPROVED"
    ABSENTEE_MARKED = "ABSENTEE_MARKED"
    DAY_LOCKED = "DAY_LOCKED"
    DAY_UNLOCKED = "DAY_UNLOCKED"
    AUTO_CHECKOUT = "AUTO_CHECKOUT"


class AttendanceLog(Base):
    """Append-only audit row. No update / delete."""
    __tablename__ = "hr_attendance_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(Enum(AttendanceLogAction, name="hr_attendance_log_action"), nullable=False, index=True)
    target_table = Column(String(80), nullable=True)
    target_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="SET NULL"), nullable=True, index=True)
    payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_hr_attlog_emp_action", "employee_id", "action"),
    )
