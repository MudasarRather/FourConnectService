"""Half-day request — employee asks for FIRST or SECOND half off.

The model mirrors WfhRequest in shape but tracks a single date plus which half
(first/second). Approval flow: PENDING → APPROVED / REJECTED / CANCELLED.

When approved, ``daily_rollup`` short-circuits the attendance status to
``HALF_DAY`` for that date — see ``app/utils/hr/attendance_logic.py``.

Two creation paths:

  * **Employee request** — user submits via ``POST /hr/half-day/me``; status
    starts PENDING, awaits admin decision.
  * **Admin manual tag** — admin force-creates an already-APPROVED row via
    ``POST /hr/half-day/`` (the manual override the user asked for on the
    daily roster). ``manager_approved_by_id`` is set to the admin and
    ``decision_notes`` carries the override reason.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class HalfDayStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class HalfDayWhich(str, enum.Enum):
    """Which half of the day is being taken off."""
    FIRST = "FIRST"     # employee away in the morning, works second half
    SECOND = "SECOND"   # employee works morning, leaves after midday


class HalfDayReason(str, enum.Enum):
    """High-level reason buckets — keeps the analytics aggregable."""
    PERSONAL = "PERSONAL"
    MEDICAL = "MEDICAL"
    FAMILY = "FAMILY"
    OFFICIAL = "OFFICIAL"
    OTHER = "OTHER"


class HalfDayRequest(Base):
    __tablename__ = "hr_half_day_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)

    half_day_date = Column(Date, nullable=False, index=True)
    which_half = Column(Enum(HalfDayWhich, name="hr_half_day_which"), nullable=False, default=HalfDayWhich.SECOND)
    reason_type = Column(Enum(HalfDayReason, name="hr_half_day_reason"), nullable=False, default=HalfDayReason.PERSONAL)
    reason = Column(Text, nullable=False)
    status = Column(Enum(HalfDayStatus, name="hr_half_day_status"), nullable=False, default=HalfDayStatus.PENDING, index=True)

    # Set on approval/rejection OR on admin manual creation.
    manager_approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    manager_approved_at = Column(DateTime(timezone=True), nullable=True)
    decision_notes = Column(Text, nullable=True)

    # Distinguishes admin-overridden rows (no employee request) from regular
    # PENDING→APPROVED flow. Useful for audit + UI badges.
    is_admin_override = Column(Boolean, nullable=False, default=False)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_halfday_emp_date", "employee_id", "half_day_date"),
    )
