"""HR Holiday Shifts — essential staff working on a holiday + their compensation.

Links an employee to a holiday (and optionally a shift) with a compensation
rule (double-pay / comp-off / holiday allowance / overtime). The pay engine can
later read these rows; for now it is a managed register with a multiplier.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Text, Index, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class HolidayCompType(str, enum.Enum):
    DOUBLE_PAY = "DOUBLE_PAY"
    COMP_OFF = "COMP_OFF"
    HOLIDAY_ALLOWANCE = "HOLIDAY_ALLOWANCE"
    OVERTIME = "OVERTIME"


class HolidayShiftAssignment(Base):
    __tablename__ = "hr_holiday_shift_assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    holiday_id = Column(UUID(as_uuid=True), ForeignKey("hr_holidays.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="SET NULL"), nullable=True)
    compensation = Column(Enum(HolidayCompType, name="hr_holiday_comp_type"), nullable=False, default=HolidayCompType.DOUBLE_PAY)
    pay_multiplier = Column(Numeric(4, 2), nullable=False, default=2.0)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Stand-down audit — captured by the Holiday Roster "remove" modal so a removal
    # records WHY (category + free note + who + when) instead of vanishing. Set on
    # soft-delete; cleared when the same (holiday, employee) pair is re-assigned
    # (a tombstoned row is resurrected rather than re-inserted — uq_holiday_emp does
    # not account for is_deleted, so re-INSERT would violate the unique constraint).
    removal_reason = Column(Text, nullable=True)
    removal_category = Column(String(64), nullable=True)
    removed_at = Column(DateTime(timezone=True), nullable=True)
    removed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    holiday = relationship("Holiday", foreign_keys=[holiday_id])
    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        UniqueConstraint("holiday_id", "employee_id", name="uq_holiday_emp"),
        Index("ix_hr_holiday_shift", "holiday_id", "employee_id"),
    )
