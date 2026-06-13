"""HR Shift Rotation — cyclic shift schedules for rotating workforces.

A `ShiftRotation` defines an ordered list of steps (each step = a shift, or OFF
when ``shift_id`` is NULL) plus the set of member employees the cycle applies
to. Advancing a rotation (``POST /hr/shift-rotations/{id}/advance``) materialises
the *current* step for each member into a concrete ``EmployeeShiftAssignment``
window, then bumps ``current_step_index``.

This complements — does not replace — ad-hoc assignment in
``app.models.hr.shift.EmployeeShiftAssignment``; rotations are just an
automated writer of those rows.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class RotationCycle(str, enum.Enum):
    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    MONTHLY = "MONTHLY"
    CUSTOM = "CUSTOM"


class ShiftRotation(Base):
    """A cyclic rotation schedule (e.g. Week1 Morning → Week2 Evening → Week3 Night)."""
    __tablename__ = "hr_shift_rotations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    code = Column(String(40), unique=True, nullable=True, index=True)
    cycle = Column(Enum(RotationCycle, name="hr_rotation_cycle"), nullable=False, default=RotationCycle.WEEKLY)
    # Period length in days for one step. For WEEKLY=7, BIWEEKLY=14, MONTHLY≈30,
    # CUSTOM uses this value directly. Resolved server-side from `cycle` unless CUSTOM.
    frequency_days = Column(Integer, nullable=False, default=7)
    description = Column(String(500), nullable=True)
    # Applicable departments — list of department UUID strings ([] = any).
    department_ids = Column(JSONB, nullable=False, default=list)
    anchor_date = Column(Date, nullable=True)  # day step 0 began
    current_step_index = Column(Integer, nullable=False, default=0)
    last_advanced_on = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    steps = relationship(
        "ShiftRotationStep", back_populates="rotation",
        cascade="all, delete-orphan", order_by="ShiftRotationStep.sequence",
    )
    members = relationship(
        "ShiftRotationMember", back_populates="rotation",
        cascade="all, delete-orphan",
    )


class ShiftRotationStep(Base):
    """One ordered step in a rotation. ``shift_id is None`` means an OFF block."""
    __tablename__ = "hr_shift_rotation_steps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    rotation_id = Column(UUID(as_uuid=True), ForeignKey("hr_shift_rotations.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False, default=0)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="RESTRICT"), nullable=True)
    label = Column(String(60), nullable=True)

    rotation = relationship("ShiftRotation", back_populates="steps")
    shift = relationship("Shift", foreign_keys=[shift_id])

    __table_args__ = (
        Index("ix_hr_rotation_step_seq", "rotation_id", "sequence"),
    )


class ShiftRotationMember(Base):
    """An employee enrolled in a rotation. ``phase_offset`` lets members sit on
    different steps of the same cycle (so the team is never all on Night at once)."""
    __tablename__ = "hr_shift_rotation_members"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    rotation_id = Column(UUID(as_uuid=True), ForeignKey("hr_shift_rotations.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    phase_offset = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    rotation = relationship("ShiftRotation", back_populates="members")
    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_rotation_member", "rotation_id", "employee_id"),
    )
