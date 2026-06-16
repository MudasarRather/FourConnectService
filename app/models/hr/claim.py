"""HR Reimbursements — the unified Claim entity.

ONE model covers every claim type (Travel / Medical / Internet / Food / Fuel /
…). Type-specific fields live in the ``details`` JSONB, validated against the
category's ``field_schema``. The approval workflow is a configurable N-stage
chain snapshotted onto ``approval_steps`` (+ ``current_step``) at submit time —
the exact mechanism the Leave module uses. Settlement folds into payroll (via a
PayrollAdjustment) or is disbursed directly (bank/cash/cheque/petty cash).

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Enum, Numeric, Integer, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.reimbursement_type import ClaimStatus, SettlementMethod


class Claim(Base):
    __tablename__ = "hr_claims"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    claim_number = Column(String(20), nullable=False, unique=True, index=True)   # CL-{YY}-{NNNNNN}

    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("hr_claim_categories.id", ondelete="RESTRICT"), nullable=False, index=True)

    # ── Common claim fields ──
    claim_date = Column(Date, nullable=False, index=True)       # date the claim was raised
    expense_date = Column(Date, nullable=False, index=True)     # date the expense was incurred
    amount = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String(3), nullable=False, default="INR")
    description = Column(Text, nullable=True)
    vendor = Column(String(160), nullable=True)
    remarks = Column(Text, nullable=True)
    cost_center = Column(String(120), nullable=True)            # free-text (matches expense.py)
    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)

    # Attachments: list of {file_url, file_path, original_filename, file_size, mime_type, doc_type}
    attachments = Column(JSONB, nullable=False, default=list)
    # Dynamic per-category fields, validated against category.field_schema
    details = Column(JSONB, nullable=False, default=dict)

    status = Column(Enum(ClaimStatus, name="hr_claim_status"), nullable=False, default=ClaimStatus.DRAFT, index=True)

    # ── Submission ──
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    submitted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # ── Configurable approval chain (snapshot) ──
    approval_steps = Column(JSONB, nullable=False, default=list)
    current_step = Column(Integer, nullable=False, default=0)
    # Denormalised final-approval stamp (mirrored from the last APPROVED step)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approver_notes = Column(Text, nullable=True)

    # ── Return / reject / clarify / cancel ──
    returned_at = Column(DateTime(timezone=True), nullable=True)
    return_reason = Column(Text, nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    reject_reason = Column(Text, nullable=True)
    clarification_note = Column(Text, nullable=True)
    clarification_requested_at = Column(DateTime(timezone=True), nullable=True)
    clarification_requested_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    cancelled_reason = Column(Text, nullable=True)

    # ── Settlement ──
    approved_amount = Column(Numeric(12, 2), nullable=True)     # approver may settle less than claimed
    settlement_method = Column(Enum(SettlementMethod, name="hr_claim_settlement_method"), nullable=True)
    settled_at = Column(DateTime(timezone=True), nullable=True)
    settled_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    settlement_number = Column(String(30), nullable=True)
    payroll_adjustment_id = Column(UUID(as_uuid=True), ForeignKey("hr_payroll_adjustments.id", ondelete="SET NULL"), nullable=True)
    payroll_ref = Column(String(80), nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)

    # ── Reversal (clawback) ──
    reversed_at = Column(DateTime(timezone=True), nullable=True)
    reversed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reversal_reason = Column(Text, nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])
    category = relationship("ClaimCategory", foreign_keys=[category_id])

    __table_args__ = (
        Index("ix_hr_claim_emp_status", "employee_id", "status"),
        Index("ix_hr_claim_cat_status", "category_id", "status"),
        Index("ix_hr_claim_status_settle", "status", "settlement_method"),
        Index("ix_hr_claim_expense_date", "expense_date"),
    )

    def __repr__(self):
        return f"<Claim {self.claim_number} {self.status}>"
