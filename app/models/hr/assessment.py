"""HR Training & Development — Assessments (quiz / exam grading engine).

An assessment is attached to a training program; a passing result auto-completes
the enrollment through the single ``complete_assignment`` path.

New tables — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Integer, Numeric, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class AssessmentType(str, enum.Enum):
    QUIZ = "QUIZ"
    EXAM = "EXAM"
    PRACTICAL = "PRACTICAL"
    SURVEY = "SURVEY"


class Assessment(Base):
    __tablename__ = "hr_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    assessment_type = Column(Enum(AssessmentType, name="hr_assessment_type"), nullable=False, default=AssessmentType.QUIZ)
    pass_score = Column(Numeric(5, 2), nullable=False, default=60)
    max_score = Column(Numeric(5, 2), nullable=False, default=100)
    max_attempts = Column(Integer, nullable=True)
    duration_minutes = Column(Integer, nullable=True)
    questions = Column(JSONB, nullable=True)  # optional question bank (Phase 2.1)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class AssessmentResult(Base):
    __tablename__ = "hr_assessment_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("hr_assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    # Links the attempt to the enrollment whose completion it gates.
    assignment_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_assignments.id", ondelete="SET NULL"), nullable=True, index=True)
    attempt_number = Column(Integer, nullable=False, default=1)
    score = Column(Numeric(5, 2), nullable=True)
    passed = Column(Boolean, nullable=True)
    answers = Column(JSONB, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    graded_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("assessment_id", "employee_id", "attempt_number", name="uq_hr_assess_attempt"),
    )
