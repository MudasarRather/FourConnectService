"""HR Payroll — Monthly Payroll Batch (the pay-run state machine).

State flow (enforced in the service layer, not the DB):

    DRAFT → GENERATED → VERIFIED → APPROVED → RELEASED → LOCKED
    GENERATED/VERIFIED → DRAFT          (reopen)
    VERIFIED/APPROVED  → GENERATED      (return for recalc)
    (any non-terminal)  → CANCELLED

``config_snapshot`` freezes the resolved statutory rates at generation time so a
re-print months later reproduces the original numbers even after rates change.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric,
    Integer, Text, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class PayrollBatchStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    GENERATED = "GENERATED"
    VERIFIED = "VERIFIED"
    APPROVED = "APPROVED"
    RELEASED = "RELEASED"
    LOCKED = "LOCKED"
    CANCELLED = "CANCELLED"


class PayrollBatch(Base):
    __tablename__ = "hr_payroll_batches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    batch_no = Column(String(30), nullable=False, unique=True, index=True)  # PR-{YY}-{MM}-{NNN}

    period_month = Column(Integer, nullable=False)   # 1-12
    period_year = Column(Integer, nullable=False)
    pay_date = Column(Date, nullable=True)

    status = Column(Enum(PayrollBatchStatus, name="hr_payroll_batch_status"), nullable=False, default=PayrollBatchStatus.DRAFT, index=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id", ondelete="SET NULL"), nullable=True)  # scope; null = all

    total_employees = Column(Integer, nullable=False, default=0)
    total_gross = Column(Numeric(16, 2), nullable=False, default=0)
    total_deductions = Column(Numeric(16, 2), nullable=False, default=0)
    total_net = Column(Numeric(16, 2), nullable=False, default=0)
    total_employer_cost = Column(Numeric(16, 2), nullable=False, default=0)

    notes = Column(Text, nullable=True)
    config_snapshot = Column(JSONB, nullable=True)  # frozen statutory rates at generation

    # Per-transition stamps
    generated_at = Column(DateTime(timezone=True), nullable=True)
    generated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verified_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    released_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    locked_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    cancel_reason = Column(String(300), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    last_updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    payslips = relationship("Payslip", back_populates="batch", cascade="all, delete-orphan")
    department = relationship("Department", foreign_keys=[department_id])

    __table_args__ = (
        UniqueConstraint("period_month", "period_year", "department_id", name="uq_hr_payroll_period_scope"),
        Index("ix_hr_payroll_period", "period_year", "period_month"),
        Index("ix_hr_payroll_status", "status", "is_deleted"),
    )

    def __repr__(self):
        return f"<PayrollBatch {self.batch_no} {self.status}>"
