"""HR Exit Management — Full & Final (F&F) Settlement.

1-1 with an exit case. Modeled on ``TravelSettlement`` for its proven idempotent
payroll-posting + reversal pattern.

    net_amount = total_earnings − total_recoveries

A positive net posts an ARREAR earning (sub_type ``FNF_SETTLEMENT:{case}``); the
recoveries post a DEDUCTION (sub_type ``FNF_RECOVERY:{case}``). The two adjustment
FKs being non-null after a successful PAY is the idempotency key — a duplicate
``pay`` is a no-op. ``computation_snapshot`` stores the full reproducible per-line
breakdown + formulae. New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Integer, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.exit_type import SettlementStatus


class ExitSettlement(Base):
    __tablename__ = "hr_exit_settlements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    settlement_number = Column(String(20), nullable=False, unique=True, index=True)   # FF-{YY}-{NNNNNN}
    exit_case_id = Column(UUID(as_uuid=True), ForeignKey("hr_exit_cases.id", ondelete="CASCADE"),
                          nullable=False, unique=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"),
                         nullable=False, index=True)

    status = Column(Enum(SettlementStatus, name="hr_exit_settlement_status"), nullable=False,
                    default=SettlementStatus.DRAFT, index=True)

    # ─── Earnings ───
    pending_salary = Column(Numeric(12, 2), nullable=False, default=0)
    leave_encashment_amount = Column(Numeric(12, 2), nullable=False, default=0)
    leave_encashment_days = Column(Numeric(6, 2), nullable=False, default=0)
    incentives_amount = Column(Numeric(12, 2), nullable=False, default=0)
    bonus_amount = Column(Numeric(12, 2), nullable=False, default=0)
    reimbursements_amount = Column(Numeric(12, 2), nullable=False, default=0)
    gratuity_amount = Column(Numeric(12, 2), nullable=False, default=0)
    other_earnings = Column(Numeric(12, 2), nullable=False, default=0)
    total_earnings = Column(Numeric(14, 2), nullable=False, default=0)

    # ─── Recoveries ───
    notice_recovery = Column(Numeric(12, 2), nullable=False, default=0)
    loan_recovery = Column(Numeric(12, 2), nullable=False, default=0)
    advance_recovery = Column(Numeric(12, 2), nullable=False, default=0)
    asset_recovery = Column(Numeric(12, 2), nullable=False, default=0)
    other_deductions = Column(Numeric(12, 2), nullable=False, default=0)
    total_recoveries = Column(Numeric(14, 2), nullable=False, default=0)

    net_amount = Column(Numeric(14, 2), nullable=False, default=0)   # may be negative (employee owes)
    currency = Column(String(3), nullable=False, default="INR")
    computation_snapshot = Column(JSONB, nullable=False, default=dict)

    # ─── Lifecycle actors ───
    verified_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verify_notes = Column(Text, nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    paid_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)

    settlement_method = Column(String(20), nullable=True)   # PAYROLL | BANK_TRANSFER | CASH

    # ─── Full & Final acknowledgement (HR clearance "Full & Final acknowledged"
    # gate writes here so the ack lives on the authoritative settlement record,
    # not just a clearance checkbox). Each timestamp is set when its task is
    # completed; ``ff_ack_snapshot`` stores the reproducible checklist + actor. ───
    ff_statement_shared_at = Column(DateTime(timezone=True), nullable=True)
    ff_acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    ff_acknowledged_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    payout_confirmed_at = Column(DateTime(timezone=True), nullable=True)
    ff_ack_snapshot = Column(JSONB, nullable=True)

    # ─── Payroll posting (idempotency key) ───
    earning_adjustment_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_adjustments.id", ondelete="SET NULL"), nullable=True)
    deduction_adjustment_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_adjustments.id", ondelete="SET NULL"), nullable=True)
    payroll_ref = Column(String(80), nullable=True)
    period_month = Column(Integer, nullable=True)
    period_year = Column(Integer, nullable=True)

    reversed_at = Column(DateTime(timezone=True), nullable=True)
    reversal_reason = Column(Text, nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    exit_case = relationship("ExitCase", back_populates="settlement")
    employee = relationship("Employee", foreign_keys=[employee_id])

    def __repr__(self):
        return f"<ExitSettlement {self.settlement_number} {self.status} net={self.net_amount}>"
