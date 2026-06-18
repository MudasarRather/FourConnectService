"""HR Training & Development — Employee training requests.

An employee requests a training (a named program or a free-text external course);
the request walks a configurable approval chain (MANAGER -> HR by default),
snapshotted onto ``approval_steps`` exactly like the Reimbursements/Leave chain.
When fully approved, HR fulfils it by creating a ``TrainingAssignment``.

New table — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Text, Numeric,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class TrainingRequestStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"
    FULFILLED = "FULFILLED"


class TrainingRequestDecision(str, enum.Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETURNED = "RETURNED"
    SKIPPED = "SKIPPED"


class TrainingRequest(Base):
    __tablename__ = "hr_training_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    request_number = Column(String(40), nullable=False, unique=True, index=True)  # "TR-25-000001"
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="SET NULL"), nullable=True, index=True)

    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    justification = Column(Text, nullable=True)
    external_provider = Column(String(200), nullable=True)
    estimated_cost = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(8), nullable=False, default="INR")
    preferred_start_date = Column(Date, nullable=True)

    status = Column(
        Enum(TrainingRequestStatus, name="hr_training_request_status"),
        nullable=False, default=TrainingRequestStatus.DRAFT, index=True,
    )
    approval_steps = Column(JSONB, nullable=True)
    current_step = Column(Integer, nullable=False, default=0)

    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approver_notes = Column(String(400), nullable=True)
    reject_reason = Column(String(400), nullable=True)
    return_reason = Column(String(400), nullable=True)

    resulting_assignment_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_assignments.id", ondelete="SET NULL"), nullable=True)

    submitted_at = Column(DateTime(timezone=True), nullable=True)
    submitted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
