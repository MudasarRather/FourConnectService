"""HR Travel — Expense Settlement (post-travel financial closure).

Reconciles the released advance against the employee's actual post-travel
expenses + approved DA:

    payable     = max(0, (approved_expense + da_amount) − advance_received)
    recoverable = max(0, advance_received − (approved_expense + da_amount))

A net payable posts to payroll as an earning (sub_type ``TRAVEL_SETTLEMENT``); a
net recoverable posts as a deduction. Expense line items live in ``expense_lines``
JSONB (one per receipt: category / date / vendor / amount / gst / attachments).

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.travel_type import TravelSettlementStatus, TravelSettlementMethod


class TravelSettlement(Base):
    __tablename__ = "hr_travel_settlements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    settlement_number = Column(String(20), nullable=False, unique=True, index=True)   # TS-{YY}-{NNNNNN}
    travel_request_id = Column(UUID(as_uuid=True), ForeignKey("hr_travel_requests.id", ondelete="CASCADE"),
                               nullable=False, unique=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)

    # Expense line items: [{category, expense_date, vendor, amount, gst, currency, attachments:[...]}]
    expense_lines = Column(JSONB, nullable=False, default=list)

    advance_received = Column(Numeric(12, 2), nullable=False, default=0)
    total_expense = Column(Numeric(12, 2), nullable=False, default=0)
    approved_expense = Column(Numeric(12, 2), nullable=False, default=0)
    da_amount = Column(Numeric(12, 2), nullable=False, default=0)
    payable_amount = Column(Numeric(12, 2), nullable=False, default=0)     # company owes employee
    recoverable_amount = Column(Numeric(12, 2), nullable=False, default=0)  # employee owes company
    currency = Column(String(3), nullable=False, default="INR")

    status = Column(Enum(TravelSettlementStatus, name="hr_travel_settlement_status"), nullable=False,
                    default=TravelSettlementStatus.DRAFT, index=True)
    settlement_method = Column(Enum(TravelSettlementMethod, name="hr_travel_settlement_method"), nullable=True)

    submitted_at = Column(DateTime(timezone=True), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    verified_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    verify_notes = Column(Text, nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    settled_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    payroll_adjustment_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_adjustments.id", ondelete="SET NULL"), nullable=True)
    payroll_ref = Column(String(80), nullable=True)
    reversed_at = Column(DateTime(timezone=True), nullable=True)
    reversal_reason = Column(Text, nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    request = relationship("TravelRequest", foreign_keys=[travel_request_id])

    def __repr__(self):
        return f"<TravelSettlement {self.settlement_number} {self.status}>"
