"""HR Payroll — Payroll Adjustments (Phase B).

One model covers Bonus / Incentive / Variable Pay / Arrear / ad-hoc Deduction —
every "one-off amount posted to a pay run". An APPROVED, unpaid adjustment whose
period matches a batch (or is unscoped) is folded into that employee's payslip as
an extra earning (or deduction) line, then marked PAID with a payroll_ref on
release — mirroring how leave encashment posts.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric,
    Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class AdjustmentType(str, enum.Enum):
    BONUS = "BONUS"
    INCENTIVE = "INCENTIVE"
    VARIABLE_PAY = "VARIABLE_PAY"
    ARREAR = "ARREAR"
    DEDUCTION = "DEDUCTION"


class AdjustmentStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class PayrollAdjustment(Base):
    __tablename__ = "hr_payroll_adjustments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)

    adjustment_type = Column(Enum(AdjustmentType, name="hr_adjustment_type"), nullable=False, index=True)
    sub_type = Column(String(60), nullable=True)   # Festival / Performance / Sales / Referral / …
    title = Column(String(160), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    is_taxable = Column(Boolean, nullable=False, default=True)
    is_deduction = Column(Boolean, nullable=False, default=False)  # subtract from net instead of adding

    # Pay-run targeting — null period = picked up by the next available run.
    period_month = Column(Integer, nullable=True)
    period_year = Column(Integer, nullable=True)

    # Arrear window (informational)
    from_date = Column(Date, nullable=True)
    to_date = Column(Date, nullable=True)

    reason = Column(Text, nullable=True)
    status = Column(Enum(AdjustmentStatus, name="hr_adjustment_status"), nullable=False, default=AdjustmentStatus.DRAFT, index=True)

    payroll_ref = Column(String(80), nullable=True)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_batches.id", ondelete="SET NULL"), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_adj_emp_status", "employee_id", "status"),
        Index("ix_hr_adj_period", "period_year", "period_month"),
    )

    def __repr__(self):
        return f"<PayrollAdjustment {self.adjustment_type} {self.amount}>"
