"""HR Performance — Performance Improvement Plans (PIP).

A structured, time-boxed plan for an underperforming employee: clear expectations,
measurable objectives, scheduled check-ins, support offered, and a final outcome.
Typically opened off the back of a low review score but can stand alone.

Workflow:
    DRAFT → ACTIVE → (EXTENDED) → SUCCESSFUL | UNSUCCESSFUL   (+ CANCELLED)

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class PipStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    EXTENDED = "EXTENDED"
    SUCCESSFUL = "SUCCESSFUL"
    UNSUCCESSFUL = "UNSUCCESSFUL"
    CANCELLED = "CANCELLED"


OPEN_PIP_STATUSES = (PipStatus.DRAFT, PipStatus.ACTIVE, PipStatus.EXTENDED)


class PerformancePip(Base):
    __tablename__ = "hr_performance_pips"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    review_id = Column(UUID(as_uuid=True), ForeignKey("hr_performance_reviews.id", ondelete="SET NULL"), nullable=True, index=True)
    manager_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    title = Column(String(200), nullable=False)
    reason = Column(Text, nullable=True)               # why the PIP was opened
    expectations = Column(Text, nullable=True)         # the standard the employee must reach
    support = Column(Text, nullable=True)              # coaching / resources offered

    status = Column(String(16), nullable=False, default=PipStatus.DRAFT.value, index=True)
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)

    # [{ "title": str, "measure": str, "target": str, "status": "OPEN|MET|MISSED" }]
    objectives_json = Column(JSONB, nullable=True)
    # [{ "at": iso, "note": str, "rating": str, "by": str }]
    check_ins_json = Column(JSONB, nullable=True)

    outcome = Column(Text, nullable=True)              # closing summary

    # employee receipt — set when the subject acknowledges an active plan (self-service)
    employee_ack_at = Column(DateTime(timezone=True), nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    employee = relationship("Employee", foreign_keys=[employee_id])
    manager = relationship("User", foreign_keys=[manager_id])

    def __repr__(self):
        return f"<PerformancePip {self.employee_id} {self.status}>"
