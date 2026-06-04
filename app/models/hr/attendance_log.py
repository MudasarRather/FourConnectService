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
    # Leave & Absence — Phase 2.x. Values added live via add_leave_module_tables.py
    LEAVE_REQUESTED = "LEAVE_REQUESTED"
    LEAVE_MANAGER_APPROVED = "LEAVE_MANAGER_APPROVED"
    LEAVE_MANAGER_REJECTED = "LEAVE_MANAGER_REJECTED"
    LEAVE_HR_APPROVED = "LEAVE_HR_APPROVED"
    LEAVE_HR_REJECTED = "LEAVE_HR_REJECTED"
    LEAVE_CANCELLED = "LEAVE_CANCELLED"
    LEAVE_WITHDRAWN = "LEAVE_WITHDRAWN"
    LEAVE_ADMIN_OVERRIDE = "LEAVE_ADMIN_OVERRIDE"
    LEAVE_BALANCE_ACCRUED = "LEAVE_BALANCE_ACCRUED"
    LEAVE_BALANCE_CARRY_FORWARD = "LEAVE_BALANCE_CARRY_FORWARD"
    LEAVE_BALANCE_ADJUSTED = "LEAVE_BALANCE_ADJUSTED"
    LEAVE_PROOF_DELETED = "LEAVE_PROOF_DELETED"
    # Phase 4.x — leave policy lifecycle (create / edit / soft-delete)
    LEAVE_POLICY_CREATED = "LEAVE_POLICY_CREATED"
    LEAVE_POLICY_UPDATED = "LEAVE_POLICY_UPDATED"
    LEAVE_POLICY_DELETED = "LEAVE_POLICY_DELETED"
    # Phase 2 — comp-off & encashment
    COMP_OFF_EARNED = "COMP_OFF_EARNED"
    COMP_OFF_GRANTED = "COMP_OFF_GRANTED"     # admin manual grant
    COMP_OFF_REVOKED = "COMP_OFF_REVOKED"     # admin deleted a grant (balance reversed)
    COMP_OFF_USED = "COMP_OFF_USED"
    COMP_OFF_EXPIRED = "COMP_OFF_EXPIRED"
    ENCASHMENT_REQUESTED = "ENCASHMENT_REQUESTED"
    ENCASHMENT_APPROVED = "ENCASHMENT_APPROVED"
    ENCASHMENT_REJECTED = "ENCASHMENT_REJECTED"
    ENCASHMENT_PAID = "ENCASHMENT_PAID"
    ENCASHMENT_CANCELLED = "ENCASHMENT_CANCELLED"


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
