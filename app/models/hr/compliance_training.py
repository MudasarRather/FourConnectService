"""HR Training & Development — Compliance training configuration.

Marks a ``TrainingProgram`` as a recurring mandatory compliance training and
configures its cadence + eligibility. The compliance engine reads these rows to
auto-reassign the training when an employee's last completion is due to expire.

New table — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Integer, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class ComplianceFrequency(str, enum.Enum):
    ONE_TIME = "ONE_TIME"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    HALF_YEARLY = "HALF_YEARLY"
    ANNUAL = "ANNUAL"
    BIENNIAL = "BIENNIAL"


# Whole-months step per frequency (used to compute next-due from last completion).
FREQUENCY_MONTHS = {
    ComplianceFrequency.ONE_TIME: None,
    ComplianceFrequency.MONTHLY: 1,
    ComplianceFrequency.QUARTERLY: 3,
    ComplianceFrequency.HALF_YEARLY: 6,
    ComplianceFrequency.ANNUAL: 12,
    ComplianceFrequency.BIENNIAL: 24,
}


class ComplianceTraining(Base):
    __tablename__ = "hr_compliance_trainings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    frequency = Column(Enum(ComplianceFrequency, name="hr_compliance_frequency"), nullable=False, default=ComplianceFrequency.ANNUAL)
    validity_months = Column(Integer, nullable=True)
    grace_period_days = Column(Integer, nullable=False, default=0)
    # Eligibility scope; same JSON shape as ClaimPolicy.eligibility.
    # null / empty -> all active employees.
    applies_to = Column(JSONB, nullable=True)
    auto_reassign = Column(Boolean, default=True, nullable=False)
    due_days_after_assign = Column(Integer, nullable=False, default=30)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("program_id", name="uq_hr_compliance_program"),
    )
