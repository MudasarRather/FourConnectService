"""HR Training & Development — Post-training feedback / ratings.

One feedback row per (assignment, employee) — the unique constraint makes
submission idempotent. Also rolls up into ``Trainer.rating_avg`` / ``rating_count``.

New table — auto-created on startup via ``Base.metadata.create_all``.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Integer, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.database import Base


class TrainingFeedback(Base):
    __tablename__ = "hr_training_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    program_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_programs.id", ondelete="CASCADE"), nullable=True, index=True)
    assignment_id = Column(UUID(as_uuid=True), ForeignKey("hr_training_assignments.id", ondelete="SET NULL"), nullable=True, index=True)
    trainer_id = Column(UUID(as_uuid=True), ForeignKey("hr_trainers.id", ondelete="SET NULL"), nullable=True, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)

    rating = Column(Integer, nullable=False)             # overall 1-5
    content_rating = Column(Integer, nullable=True)
    trainer_rating = Column(Integer, nullable=True)
    relevance_rating = Column(Integer, nullable=True)
    comments = Column(Text, nullable=True)
    would_recommend = Column(Boolean, nullable=True)
    is_anonymous = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("assignment_id", "employee_id", name="uq_hr_feedback_assignment_emp"),
    )
