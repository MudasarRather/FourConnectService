"""HR Exit Management — Exit Interview (attrition analytics feature).

1-1 with an exit case. Captures structured ratings + free-text feedback +
rehire signal. ``responses`` holds the questionnaire ([{question, answer, rating}]);
``ratings`` holds the headline Likert scores ({management, culture, growth,
compensation, overall} 1-5). Confidential by default — raw responses are hidden
from the manager role on the self/team surfaces.

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.exit_type import InterviewStatus, ExitReasonCategory


class ExitInterview(Base):
    __tablename__ = "hr_exit_interviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    exit_case_id = Column(UUID(as_uuid=True), ForeignKey("hr_exit_cases.id", ondelete="CASCADE"),
                          nullable=False, unique=True, index=True)

    status = Column(Enum(InterviewStatus, name="hr_exit_interview_status"), nullable=False,
                    default=InterviewStatus.SCHEDULED, index=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    conducted_at = Column(DateTime(timezone=True), nullable=True)
    conducted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    mode = Column(String(20), nullable=True)   # IN_PERSON | VIDEO | FORM
    # HR's appointment instructions shown to the employee (meeting link / room / agenda).
    details = Column(Text, nullable=True)

    responses = Column(JSONB, nullable=False, default=list)   # [{question, answer, rating}]
    ratings = Column(JSONB, nullable=False, default=dict)     # {management, culture, growth, compensation, overall}
    would_recommend = Column(Boolean, nullable=True)          # eNPS signal
    primary_reason_category = Column(Enum(ExitReasonCategory, name="hr_exit_reason_category", create_type=False), nullable=True)
    feedback_summary = Column(Text, nullable=True)
    is_confidential = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    exit_case = relationship("ExitCase", back_populates="interview")
    conducted_by = relationship("User", foreign_keys=[conducted_by_id])

    def __repr__(self):
        return f"<ExitInterview case={self.exit_case_id} {self.status}>"
