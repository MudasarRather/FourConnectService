"""HR Training programs + assignments.

A program is reusable (a course definition). An assignment links one program to
one employee with completion tracking + certification.
"""
import enum
import uuid
from datetime import date
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Text, Index, Numeric,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class TrainingType(str, enum.Enum):
    HR_ORIENTATION = "HR_ORIENTATION"
    SECURITY = "SECURITY"
    SOFTWARE = "SOFTWARE"
    COMPLIANCE = "COMPLIANCE"
    SAFETY = "SAFETY"
    DEPARTMENT = "DEPARTMENT"
    OTHER = "OTHER"


class TrainingAssignmentStatus(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    WAIVED = "WAIVED"


class TrainingProgram(Base):
    __tablename__ = "hr_training_programs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(200), nullable=False, unique=True, index=True)
    code = Column(String(40), nullable=True, unique=True)
    training_type = Column(Enum(TrainingType, name="hr_training_type"), nullable=False, index=True)
    description = Column(Text, nullable=True)
    duration_hours = Column(Numeric(6, 2), nullable=True)
    trainer_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    certification_required = Column(Boolean, default=False, nullable=False)
    is_mandatory_for_new_joiners = Column(Boolean, default=False, nullable=False, index=True)
    materials_url = Column(String(600), nullable=True)
    # ── Training & Development (Phase 5) additive columns ──
    # Nullable / defaulted so existing rows + the onboarding flow are unaffected.
    # NOTE: create_all() won't ALTER existing tables — `add_training_columns.py`
    # (and ensure_training_columns() at startup) add these on the live DB.
    delivery_mode = Column(String(30), nullable=True)        # CLASSROOM | ONLINE | BLENDED | SELF_PACED | WORKSHOP | WEBINAR
    is_compliance = Column(Boolean, default=False, nullable=True)  # fast filter; authoritative config in hr_compliance_trainings
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class TrainingAssignment(Base):
    __tablename__ = "hr_training_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    process_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True, index=True)

    assigned_date = Column(Date, nullable=False, default=date.today, server_default=func.current_date())
    due_date = Column(Date, nullable=True)
    completion_date = Column(Date, nullable=True)
    status = Column(
        Enum(TrainingAssignmentStatus, name="hr_training_assignment_status"),
        nullable=False, default=TrainingAssignmentStatus.NOT_STARTED, index=True,
    )
    score = Column(Numeric(5, 2), nullable=True)
    certification_url = Column(String(600), nullable=True)
    notes = Column(Text, nullable=True)
    # ── Training & Development (Phase 5) additive columns ──
    # ONBOARDING | COMPLIANCE | REQUEST | MANUAL | SELF — distinguishes career
    # enrollments from onboarding ones (NULL == legacy/onboarding).
    enrollment_source = Column(String(30), nullable=True)
    valid_until = Column(Date, nullable=True)            # completion validity (compliance / cert expiry)
    feedback_submitted = Column(Boolean, default=False, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    program = relationship("TrainingProgram", foreign_keys=[program_id])
    employee = relationship("Employee", foreign_keys=[employee_id])
