"""HR Reimbursements — Settlement ledger.

An auditable record of every disbursement against a claim (payroll fold-in or
direct bank/cash/cheque payment). The claim itself carries the latest settlement
pointers; this table is the immutable history (and supports reversals).

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.reimbursement_type import SettlementMethod


class ClaimSettlement(Base):
    __tablename__ = "hr_claim_settlements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    settlement_number = Column(String(30), nullable=False, unique=True, index=True)  # ST-{YY}-{NNNNNN}
    claim_id = Column(UUID(as_uuid=True), ForeignKey("hr_claims.id", ondelete="CASCADE"), nullable=False, index=True)

    method = Column(Enum(SettlementMethod, name="hr_claim_settlement_method"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    settlement_date = Column(Date, nullable=True)
    reference = Column(String(120), nullable=True)        # UTR / cheque no / voucher no
    bank_account_last4 = Column(String(4), nullable=True)

    # Payroll linkage (when method == PAYROLL)
    payroll_adjustment_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_adjustments.id", ondelete="SET NULL"), nullable=True)
    payroll_ref = Column(String(80), nullable=True)

    notes = Column(Text, nullable=True)
    is_reversed = Column(Boolean, nullable=False, default=False, index=True)
    reversed_at = Column(DateTime(timezone=True), nullable=True)

    settled_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    claim = relationship("Claim", foreign_keys=[claim_id])

    def __repr__(self):
        return f"<ClaimSettlement {self.settlement_number} {self.method}>"
