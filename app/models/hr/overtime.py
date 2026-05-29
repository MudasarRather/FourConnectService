"""Overtime requests — SKELETON for Phase 2.X.

Models the workflow but the auto-calculation + payroll integration are TODO.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Text, Numeric, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class OtType(str, enum.Enum):
    WEEKDAY = "WEEKDAY"
    WEEKEND = "WEEKEND"
    HOLIDAY = "HOLIDAY"
    EMERGENCY = "EMERGENCY"


class OtStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class OtPayrollStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"


class OvertimeRequest(Base):
    __tablename__ = "hr_overtime_requests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    ot_hours = Column(Numeric(5, 2), nullable=False, default=0)
    ot_type = Column(Enum(OtType, name="hr_ot_type"), nullable=False, default=OtType.WEEKDAY)
    reason = Column(Text, nullable=True)
    status = Column(Enum(OtStatus, name="hr_ot_status"), nullable=False, default=OtStatus.PENDING, index=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    decision_notes = Column(Text, nullable=True)
    payroll_status = Column(Enum(OtPayrollStatus, name="hr_ot_payroll_status"), nullable=False, default=OtPayrollStatus.PENDING)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_ot_emp_date", "employee_id", "date"),
    )
