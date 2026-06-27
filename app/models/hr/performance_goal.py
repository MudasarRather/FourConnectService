"""HR Performance — Goals & OKRs.

An employee's objectives + key results for a cycle. An OBJECTIVE is a qualitative
aim; KEY_RESULTs (``parent_id`` → the objective) are its measurable outcomes; a
standalone GOAL tracks its own progress directly. An objective's progress is the
weighted average of its key results; a goal/KR tracks ``current_value`` against
``target_value`` (or a direct ``progress`` for milestone/boolean metrics).

Check-ins are appended to ``check_ins_json`` so progress has an auditable trail.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
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


class GoalType(str, enum.Enum):
    OBJECTIVE = "OBJECTIVE"      # qualitative aim (OKR objective)
    KEY_RESULT = "KEY_RESULT"    # measurable outcome under an objective
    GOAL = "GOAL"                # standalone goal (not OKR-structured)


class GoalStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ON_TRACK = "ON_TRACK"
    AT_RISK = "AT_RISK"
    OFF_TRACK = "OFF_TRACK"
    ACHIEVED = "ACHIEVED"
    MISSED = "MISSED"
    CANCELLED = "CANCELLED"


# Statuses considered "live" (the goal is being pursued).
OPEN_GOAL_STATUSES = (
    GoalStatus.DRAFT, GoalStatus.ON_TRACK, GoalStatus.AT_RISK, GoalStatus.OFF_TRACK,
)


class GoalMetric(str, enum.Enum):
    PERCENT = "PERCENT"
    NUMBER = "NUMBER"
    CURRENCY = "CURRENCY"
    MILESTONE = "MILESTONE"   # binary done / not-done
    BOOLEAN = "BOOLEAN"


class PerformanceGoal(Base):
    __tablename__ = "hr_performance_goals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    # subject + OKR tree
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("hr_performance_goals.id", ondelete="CASCADE"), nullable=True, index=True)

    cycle = Column(String(20), nullable=False, default="ANNUAL", index=True)
    period_label = Column(String(60), nullable=True)

    goal_type = Column(String(20), nullable=False, default=GoalType.OBJECTIVE.value)
    title = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category = Column(String(40), nullable=True)        # alignment lens — Business / Customer / People / Innovation …

    weight = Column(Numeric(5, 2), nullable=False, default=0)     # relative weight within the objective / cycle

    # measurement (for KRs / standalone goals)
    metric_type = Column(String(20), nullable=False, default=GoalMetric.PERCENT.value)
    start_value = Column(Numeric(14, 2), nullable=True, default=0)
    target_value = Column(Numeric(14, 2), nullable=True)
    current_value = Column(Numeric(14, 2), nullable=True, default=0)
    unit = Column(String(20), nullable=True)

    progress = Column(Numeric(5, 2), nullable=False, default=0)   # 0..100 (derived for KRs, rolled-up for objectives)
    status = Column(String(20), nullable=False, default=GoalStatus.DRAFT.value, index=True)

    start_date = Column(DateTime(timezone=True), nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)

    review_id = Column(UUID(as_uuid=True), ForeignKey("hr_performance_reviews.id", ondelete="SET NULL"), nullable=True, index=True)

    # [{ "at": iso, "progress": float, "note": str, "by": str, "status": str }]
    check_ins_json = Column(JSONB, nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])
    # Adjacency list: a KEY_RESULT's .parent is its OBJECTIVE; an OBJECTIVE's
    # .children are its key results. DB ondelete=CASCADE backstops ORM cascade.
    children = relationship(
        "PerformanceGoal",
        back_populates="parent",
        cascade="all, delete-orphan",
    )
    parent = relationship(
        "PerformanceGoal",
        back_populates="children",
        remote_side=[id],
    )

    def __repr__(self):
        return f"<PerformanceGoal {self.goal_type} {self.title!r} {self.progress}%>"
