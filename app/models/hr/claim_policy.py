"""HR Reimbursements — Claim Policy (per-category rules).

One row per ClaimCategory. Drives limits, attachment requirements, eligibility
scoping and — most importantly — the configurable N-stage ``approval_chain``
(snapshotted onto each claim at submit, exactly like ``LeavePolicy.approval_chain``).

New table — auto-created on startup. Default rows seeded by
``app/utils/hr/reimbursements/seeds.py``.
"""
import uuid

from sqlalchemy import (
    Column, Boolean, DateTime, ForeignKey, Enum, Integer, Numeric, UniqueConstraint, String,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.reimbursement_type import SettlementMethod


class ClaimPolicy(Base):
    __tablename__ = "hr_claim_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    category_id = Column(
        UUID(as_uuid=True), ForeignKey("hr_claim_categories.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )

    # Monetary limits (nullable = no cap)
    max_amount_per_claim = Column(Numeric(12, 2), nullable=True)
    max_amount_per_month = Column(Numeric(12, 2), nullable=True)
    max_amount_per_year = Column(Numeric(12, 2), nullable=True)
    max_claims_per_month = Column(Integer, nullable=True)

    # Attachment rules
    requires_attachment = Column(Boolean, nullable=False, default=True)
    attachment_required_above = Column(Numeric(12, 2), nullable=True)  # require receipt only above this amount

    default_settlement_method = Column(
        Enum(SettlementMethod, name="hr_claim_settlement_method"),
        nullable=False, default=SettlementMethod.PAYROLL,
    )

    # Eligibility scope. NULL / empty lists => all employees. Shape:
    #   {"department_ids": [..], "designation_ids": [..], "grade_ids": [..],
    #    "employment_types": ["FULL_TIME", ..]}
    eligibility = Column(JSONB, nullable=True)

    # Configurable approval chain. NULL => default ``[MANAGER, FINANCE, HR]``.
    # Each element: {"approver_type": "MANAGER"|"FINANCE"|"HR"|"USER",
    #                "approver_user_id": <uuid|null>, "label": <str>,
    #                "min_amount": <number|null>}  (stage applies only above min_amount)
    approval_chain = Column(JSONB, nullable=True)

    # Submission window — claims older than this many days from expense_date are blocked (null = no limit)
    submission_window_days = Column(Integer, nullable=True)

    label = Column(String(80), nullable=True)
    description = Column(String(400), nullable=True)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("category_id", name="uq_hr_claim_policy_category"),
    )

    def __repr__(self):
        return f"<ClaimPolicy category={self.category_id}>"
