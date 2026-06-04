"""HR Leave Encashment — convert unused leave into cash.

Phase 2 = calc-only (no payroll posting yet). Each request snapshots the
employee's basic salary + the formula used so the audit is reproducible even
if compensation or settings change later. Phase 3 will hook this into the
Payroll module via `paid_at` + a payroll batch reference.

Workflow:
  PENDING → APPROVED (HR) → PAID (Finance, Phase 3) → terminal
  PENDING → REJECTED → terminal
  PENDING → CANCELLED → terminal (employee withdraw)
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey,
    Enum, Numeric, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.leave_type import LeaveType, EncashmentStatus


class LeaveEncashment(Base):
    __tablename__ = "hr_leave_encashments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    reference_no = Column(String(20), nullable=False, unique=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type = Column(Enum(LeaveType, name="hr_leave_type"), nullable=False, default=LeaveType.EARNED)
    fiscal_year = Column(String(7), nullable=False, index=True)

    days_requested = Column(Numeric(5, 2), nullable=False)
    # Snapshot of monthly basic salary at request time (Employee.monthly_ctc is
    # a proxy until a Payroll module materialises; admin can override the
    # snapshot before approving).
    basic_salary_snapshot = Column(Numeric(12, 2), nullable=False, default=0)
    formula_used = Column(String(200), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False, default=0)

    status = Column(Enum(EncashmentStatus, name="hr_leave_encashment_status"), nullable=False, default=EncashmentStatus.PENDING, index=True)
    request_notes = Column(Text, nullable=True)

    # Stage 1 — reporting-manager endorsement (skipped when no/self manager).
    # manager_id holds the manager's USER id (mirrors Employee.reporting_manager_id).
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    manager_decision = Column(String(20), nullable=True)   # APPROVED | REJECTED | SKIPPED
    manager_decided_at = Column(DateTime(timezone=True), nullable=True)
    manager_notes = Column(Text, nullable=True)

    # Stage 2 — HR sanction
    decided_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    decision_notes = Column(Text, nullable=True)

    # Phase 3 will populate these
    paid_at = Column(DateTime(timezone=True), nullable=True)
    paid_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    payroll_ref = Column(String(80), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_encash_emp_status", "employee_id", "status"),
        Index("ix_hr_encash_fy", "fiscal_year"),
    )
