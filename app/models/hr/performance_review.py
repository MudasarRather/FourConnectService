"""HR Performance Management — review instances scored against appraisal templates.

This is the module the Appraisal-Templates settings page was always feeding: a
``PerformanceReview`` is one employee's review for a cycle/period, created from an
``AppraisalTemplate`` whose weighted sections + rating scale are SNAPSHOTTED onto
the review at creation (so later template edits never mutate a live review).

Workflow:
    SELF_ASSESSMENT  → employee fills self-ratings + comments, submits
    MANAGER_ASSESSMENT → reporting manager (or HR) fills manager-ratings, submits
    COMPLETED        → weighted overall score computed
    ACKNOWLEDGED     → employee signs off
    (CANCELLED)      → withdrawn by HR

Single table — section scores live in ``sections_json`` (snapshot list). New
table, auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, Date, ForeignKey, Numeric, Integer, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class PerformanceReviewStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    SELF_ASSESSMENT = "SELF_ASSESSMENT"
    MANAGER_ASSESSMENT = "MANAGER_ASSESSMENT"
    COMPLETED = "COMPLETED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    CANCELLED = "CANCELLED"


class HikeStatus(str, enum.Enum):
    NONE = "NONE"               # no recommendation yet
    RECOMMENDED = "RECOMMENDED"  # manager (or HR) proposed a hike within the band
    APPROVED = "APPROVED"        # HR approved a %, not yet pushed to payroll
    APPLIED = "APPLIED"          # compensation revision created & active
    REJECTED = "REJECTED"        # HR declined the hike


# Statuses considered "in-flight" (a review is open for action).
OPEN_REVIEW_STATUSES = (
    PerformanceReviewStatus.DRAFT,
    PerformanceReviewStatus.SELF_ASSESSMENT,
    PerformanceReviewStatus.MANAGER_ASSESSMENT,
)


class PerformanceReview(Base):
    __tablename__ = "hr_performance_reviews"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # subject + routing
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)  # reporting manager (snapshot)

    # template snapshot (review survives template deletion)
    template_id = Column(UUID(as_uuid=True), ForeignKey("hr_appraisal_templates.id"), nullable=True, index=True)
    template_code = Column(String(30), nullable=True)
    template_name = Column(String(120), nullable=True)
    cycle = Column(String(20), nullable=False, default="ANNUAL", index=True)   # ANNUAL | HALF_YEARLY | ...
    period_label = Column(String(60), nullable=True)                            # e.g. "FY 2025-26", "Q1 2026"

    rating_max = Column(Integer, nullable=False, default=5)
    rating_labels = Column(JSONB, nullable=True)                                # ["Poor", ... ] snapshot

    # the weighted sections, scored in place:
    #   [{ key, title, section_type, weight, criteria,
    #      self_rating, manager_rating, self_comment, manager_comment }]
    sections_json = Column(JSONB, nullable=True)

    status = Column(
        String(24), nullable=False, default=PerformanceReviewStatus.SELF_ASSESSMENT.value, index=True,
    )

    # rolled-up scores (out of rating_max)
    self_overall = Column(Numeric(5, 2), nullable=True)
    manager_overall = Column(Numeric(5, 2), nullable=True)
    overall_score = Column(Numeric(5, 2), nullable=True)                        # final (manager_overall once completed)

    # narrative
    self_comments = Column(Text, nullable=True)
    manager_comments = Column(Text, nullable=True)
    ack_comments = Column(Text, nullable=True)
    employee_ack = Column(Boolean, nullable=False, default=False)

    # timeline
    self_submitted_at = Column(DateTime(timezone=True), nullable=True)
    manager_submitted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)

    # ── Merit / hike outcome (Appraisal → Payroll) ──
    # The cycle's company merit policy + the date the hike takes effect, snapshotted
    # at launch. The manager recommends a % within the band the score lands in; HR
    # approves; the approved hike is applied as an effective-dated compensation
    # revision (see performance.py approve-hike → create_compensation_revision).
    merit_policy_id = Column(UUID(as_uuid=True), ForeignKey("hr_merit_policies.id"), nullable=True, index=True)
    hike_effective_from = Column(Date, nullable=True)
    final_rating_band = Column(String(40), nullable=True)        # resolved band label (calibrated > manager)
    recommended_hike_pct = Column(Numeric(5, 2), nullable=True)
    recommendation_note = Column(Text, nullable=True)
    recommended_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    recommended_at = Column(DateTime(timezone=True), nullable=True)
    approved_hike_pct = Column(Numeric(5, 2), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    hike_status = Column(String(16), nullable=False, default="NONE", index=True)  # NONE|RECOMMENDED|APPROVED|APPLIED|REJECTED
    comp_revision_id = Column(UUID(as_uuid=True), ForeignKey("hr_employee_compensations.id"), nullable=True)
    prev_annual_ctc = Column(Numeric(14, 2), nullable=True)
    new_annual_ctc = Column(Numeric(14, 2), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])
    reviewer = relationship("User", foreign_keys=[reviewer_id])

    def __repr__(self):
        return f"<PerformanceReview {self.employee_id} {self.cycle} {self.status}>"
