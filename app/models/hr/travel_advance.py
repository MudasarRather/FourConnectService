"""HR Travel — Travel Advance.

A pre-travel cash advance requested against a travel request. On RELEASE the
amount posts to payroll as a PayrollAdjustment (sub_type ``TRAVEL_ADVANCE``) and
is reconciled in the final expense settlement (advance vs. spend).

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.travel_type import AdvanceStatus, TravelSettlementMethod


class TravelAdvance(Base):
    __tablename__ = "hr_travel_advances"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    advance_number = Column(String(20), nullable=False, unique=True, index=True)   # AD-{YY}-{NNNNNN}
    travel_request_id = Column(UUID(as_uuid=True), ForeignKey("hr_travel_requests.id", ondelete="CASCADE"),
                               nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)

    advance_amount = Column(Numeric(12, 2), nullable=False, default=0)
    approved_amount = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(3), nullable=False, default="INR")
    purpose = Column(Text, nullable=True)

    status = Column(Enum(AdvanceStatus, name="hr_travel_advance_status"), nullable=False,
                    default=AdvanceStatus.REQUESTED, index=True)

    # How the approved advance is disbursed. PAYROLL posts a TRAVEL_ADVANCE payroll
    # adjustment; BANK_TRANSFER / CASH / CHEQUE are paid directly by treasury (no payroll
    # posting) with a reference. In every case the advance is recovered at settlement.
    # Reuses the settlement-method enum type — hence create_type=False (owned by travel_settlement).
    disbursement_method = Column(
        Enum(TravelSettlementMethod, name="hr_travel_settlement_method", create_type=False),
        nullable=False, default=TravelSettlementMethod.PAYROLL, server_default="PAYROLL")
    disbursement_reference = Column(String(120), nullable=True)   # bank UTR / cash voucher / cheque no.

    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    released_at = Column(DateTime(timezone=True), nullable=True)
    released_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    recovered_amount = Column(Numeric(12, 2), nullable=True)
    reject_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)

    payroll_adjustment_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_adjustments.id", ondelete="SET NULL"), nullable=True)
    payroll_ref = Column(String(80), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    request = relationship("TravelRequest", foreign_keys=[travel_request_id])

    def __repr__(self):
        return f"<TravelAdvance {self.advance_number} {self.status}>"
