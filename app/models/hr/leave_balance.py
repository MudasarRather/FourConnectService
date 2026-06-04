"""HR Leave Balance — running totals per (employee, leave_type, fiscal_year).

Recomputed at write-time by `_apply_ledger` in the router: every ledger row
mutates the corresponding balance row in the same transaction. `closing_balance`
is derived (`opening + accrued + carry_forward_in + adjustments - used - encashed`)
and snapshotted on each ledger application so reads never have to recompute.

Fiscal year format: "YYYY-YY" (e.g. "2026-27" for 1 Apr 2026 – 31 Mar 2027).
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Numeric, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.leave_type import LeaveType


class LeaveBalance(Base):
    """Per-employee, per-leave-type, per-fiscal-year running totals."""
    __tablename__ = "hr_leave_balances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    leave_type = Column(Enum(LeaveType, name="hr_leave_type"), nullable=False, index=True)
    fiscal_year = Column(String(7), nullable=False, index=True)   # "2026-27"

    opening_balance = Column(Numeric(6, 2), nullable=False, default=0)
    accrued = Column(Numeric(6, 2), nullable=False, default=0)
    carry_forward_in = Column(Numeric(6, 2), nullable=False, default=0)
    used = Column(Numeric(6, 2), nullable=False, default=0)
    encashed = Column(Numeric(6, 2), nullable=False, default=0)
    adjustments = Column(Numeric(6, 2), nullable=False, default=0)   # signed
    closing_balance = Column(Numeric(6, 2), nullable=False, default=0)  # snapshotted

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        UniqueConstraint("employee_id", "leave_type", "fiscal_year", name="uq_hr_balance_emp_type_fy"),
        Index("ix_hr_balance_emp_fy", "employee_id", "fiscal_year"),
    )
