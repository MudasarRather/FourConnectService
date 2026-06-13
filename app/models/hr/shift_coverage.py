"""HR Coverage Management — minimum-staffing rules + shortfall detection.

A `ShiftCoverageRule` declares the minimum head-count required for a shift
(optionally scoped to a department). The ``GET /hr/shift-coverage/alerts``
endpoint compares each rule's ``min_staff`` against the number of employees
actively assigned to that shift on a given date and returns the shortfalls.
"""
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Integer, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class ShiftCoverageRule(Base):
    """Minimum-staffing rule for a shift (optionally per-department)."""
    __tablename__ = "hr_shift_coverage_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="CASCADE"), nullable=False, index=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id", ondelete="SET NULL"), nullable=True, index=True)
    min_staff = Column(Integer, nullable=False, default=1)
    label = Column(String(120), nullable=True)
    # Flags a business-critical position (e.g. Security, IT on-call). Shortfalls
    # on critical rules are surfaced with higher severity in the UI.
    critical = Column(Boolean, nullable=False, default=False)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    shift = relationship("Shift", foreign_keys=[shift_id])

    __table_args__ = (
        Index("ix_hr_coverage_shift_dept", "shift_id", "department_id"),
    )
