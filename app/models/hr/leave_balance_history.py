"""HR Leave Balance History — append-only ledger.

Every credit/debit to a `LeaveBalance` row writes a history entry here. The
ledger captures `balance_before` + `delta` + `balance_after` so the chain can
be audited without recomputing from scratch.

Idempotency: ACCRUAL rows for a given (employee, leave_type, month) are
de-duped by router check before insert. CARRY_FORWARD rows are de-duped by
(employee, leave_type, from_fy, to_fy).
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, Date, DateTime, ForeignKey,
    Enum, Numeric, Index, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.leave_type import LeaveType, LedgerKind


class LeaveBalanceHistory(Base):
    """Immutable ledger row. No updates / deletes."""
    __tablename__ = "hr_leave_balance_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type = Column(Enum(LeaveType, name="hr_leave_type"), nullable=False, index=True)
    fiscal_year = Column(String(7), nullable=False, index=True)

    kind = Column(Enum(LedgerKind, name="hr_leave_ledger_kind"), nullable=False, index=True)
    delta = Column(Numeric(6, 2), nullable=False)            # signed: + credit, - debit
    balance_before = Column(Numeric(6, 2), nullable=False)
    balance_after = Column(Numeric(6, 2), nullable=False)

    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    note = Column(Text, nullable=True)
    related_request_id = Column(UUID(as_uuid=True), ForeignKey("hr_leave_requests.id", ondelete="SET NULL"), nullable=True, index=True)

    # Phase 2 — comp-off & encashment lineage.
    # True when daily_rollup auto-credited a COMP_OFF row (vs admin manual grant).
    is_auto_generated = Column(Boolean, nullable=False, default=False, server_default="false")
    # For COMP_OFF_EARNED rows: the source date the employee worked, and the
    # expiry date (creation + comp_off_expiry_days from settings). For
    # ENCASHMENT rows, this can stay null.
    earned_on = Column(Date, nullable=True, index=True)
    expires_on = Column(Date, nullable=True, index=True)
    related_encashment_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_ledger_emp_type_fy_at", "employee_id", "leave_type", "fiscal_year", "created_at"),
    )
