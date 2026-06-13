"""HR Weekly Roster — a per-week, per-department manpower plan.

A `ShiftRoster` is a draft grid of (employee × day → shift) entries for one
week. Publishing it (``POST /hr/shift-rosters/{id}/publish``) materialises each
dated entry into a one-day ``EmployeeShiftAssignment`` so the daily attendance
rollup honours it. Drafts are freely editable; published rosters are locked.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Numeric, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class RosterStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class ShiftRoster(Base):
    """A weekly roster header. One per (week, department) by convention."""
    __tablename__ = "hr_shift_rosters"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=True)
    week_start = Column(Date, nullable=False, index=True)  # Monday by convention
    week_end = Column(Date, nullable=False)
    department_id = Column(UUID(as_uuid=True), ForeignKey("hr_departments.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(Enum(RosterStatus, name="hr_roster_status"), nullable=False, default=RosterStatus.DRAFT, index=True)
    notes = Column(String(500), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    published_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    entries = relationship(
        "ShiftRosterEntry", back_populates="roster",
        cascade="all, delete-orphan",
    )


class ShiftRosterEntry(Base):
    """One cell of the roster grid: an employee on a given day → a shift (or OFF)."""
    __tablename__ = "hr_shift_roster_entries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    roster_id = Column(UUID(as_uuid=True), ForeignKey("hr_shift_rosters.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="RESTRICT"), nullable=True)  # NULL = OFF
    duty_hours = Column(Numeric(4, 2), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    roster = relationship("ShiftRoster", back_populates="entries")
    shift = relationship("Shift", foreign_keys=[shift_id])
    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        UniqueConstraint("roster_id", "employee_id", "day", name="uq_roster_emp_day"),
        Index("ix_hr_roster_entry_day", "roster_id", "day"),
    )
