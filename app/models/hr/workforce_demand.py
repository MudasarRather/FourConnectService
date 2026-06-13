"""HR Workforce Demand — forward-looking staffing requirement per shift.

A `WorkforceDemand` declares the headcount a shift (optionally scoped to a
department / skill) needs over an effective-dated window. The forecast endpoint
projects this demand against assigned capacity day-by-day to surface shortfalls
before they happen. Distinct from `ShiftCoverageRule` (a today-only min-staff
alert) — this is a dated forecast.
"""
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date, Integer, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class WorkforceDemand(Base):
    __tablename__ = "hr_workforce_demands"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="CASCADE"), nullable=False, index=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id", ondelete="SET NULL"), nullable=True, index=True)
    required_headcount = Column(Integer, nullable=False, default=1)
    skill = Column(String(120), nullable=True)
    valid_from = Column(Date, nullable=False, index=True)
    valid_to = Column(Date, nullable=True)   # NULL = open-ended
    notes = Column(String(400), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    shift = relationship("Shift", foreign_keys=[shift_id])

    __table_args__ = (
        Index("ix_hr_wf_demand_shift_from", "shift_id", "valid_from"),
    )
