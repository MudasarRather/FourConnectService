"""HR Performance — 360° / multi-rater feedback.

A ``PerfFeedbackRequest`` is opened for one employee (the subject) for a cycle.
Nominees (self, manager, peers, direct reports, skip-level, external) each get a
``PerfFeedbackResponse`` to fill: per-competency ratings + strengths/improvements.
Responses can be anonymous (the subject sees aggregated themes, not who said what).

The rollup (themes, average per competency, response rate) is computed in the
service layer and can be surfaced inside the related performance review.

New tables — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class FeedbackRequestStatus(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class FeedbackRelationship(str, enum.Enum):
    SELF = "SELF"
    MANAGER = "MANAGER"
    PEER = "PEER"
    DIRECT_REPORT = "DIRECT_REPORT"
    SKIP_LEVEL = "SKIP_LEVEL"
    EXTERNAL = "EXTERNAL"


class FeedbackResponseStatus(str, enum.Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    DECLINED = "DECLINED"


class PerfFeedbackRequest(Base):
    __tablename__ = "hr_perf_feedback_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    review_id = Column(UUID(as_uuid=True), ForeignKey("hr_performance_reviews.id", ondelete="SET NULL"), nullable=True, index=True)

    cycle = Column(String(20), nullable=False, default="ANNUAL", index=True)
    period_label = Column(String(60), nullable=True)
    title = Column(String(160), nullable=True)
    prompt = Column(Text, nullable=True)                 # instructions shown to raters

    # competency dimensions to rate — [{ "key": str, "label": str }]
    competencies_json = Column(JSONB, nullable=True)
    rating_max = Column(Numeric(4, 1), nullable=False, default=5)

    anonymous = Column(Boolean, nullable=False, default=True)
    status = Column(String(16), nullable=False, default=FeedbackRequestStatus.OPEN.value, index=True)
    due_date = Column(DateTime(timezone=True), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])
    responses = relationship(
        "PerfFeedbackResponse", back_populates="request",
        cascade="all, delete-orphan", order_by="PerfFeedbackResponse.created_at",
    )

    def __repr__(self):
        return f"<PerfFeedbackRequest {self.employee_id} {self.cycle} {self.status}>"


class PerfFeedbackResponse(Base):
    __tablename__ = "hr_perf_feedback_responses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    request_id = Column(UUID(as_uuid=True), ForeignKey("hr_perf_feedback_requests.id", ondelete="CASCADE"), nullable=False, index=True)

    # the rater — a system user and/or an employee record (snapshot name for display)
    reviewer_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    reviewer_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="SET NULL"), nullable=True, index=True)
    reviewer_name = Column(String(160), nullable=True)            # snapshot (for external / display)
    relationship_type = Column(String(20), nullable=False, default=FeedbackRelationship.PEER.value)

    status = Column(String(16), nullable=False, default=FeedbackResponseStatus.PENDING.value, index=True)

    # [{ "key": str, "label": str, "rating": float }]
    ratings_json = Column(JSONB, nullable=True)
    overall_rating = Column(Numeric(5, 2), nullable=True)
    strengths = Column(Text, nullable=True)
    improvements = Column(Text, nullable=True)
    comments = Column(Text, nullable=True)

    submitted_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    request = relationship("PerfFeedbackRequest", back_populates="responses")
    reviewer_user = relationship("User", foreign_keys=[reviewer_user_id])

    def __repr__(self):
        return f"<PerfFeedbackResponse {self.relationship_type} {self.status}>"
