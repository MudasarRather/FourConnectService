"""Attendance correction requests — manager → HR two-level approval.

Used when a punch is missed / biometric failed / shift mismatch. On approval
the requested in/out is applied to the linked `Attendance` row and an
`AttendanceLog` audit row is written.
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


class CorrectionStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AttendanceCorrection(Base):
    __tablename__ = "hr_attendance_corrections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    attendance_id = Column(UUID(as_uuid=True), ForeignKey("hr_attendance.id", ondelete="SET NULL"), nullable=True, index=True)
    attendance_date = Column(Date, nullable=False, index=True)

    original_check_in = Column(DateTime(timezone=True), nullable=True)
    original_check_out = Column(DateTime(timezone=True), nullable=True)
    requested_check_in = Column(DateTime(timezone=True), nullable=True)
    requested_check_out = Column(DateTime(timezone=True), nullable=True)

    reason = Column(Text, nullable=False)
    attachment_url = Column(String(500), nullable=True)
    status = Column(Enum(CorrectionStatus, name="hr_correction_status"), nullable=False, default=CorrectionStatus.PENDING, index=True)

    manager_approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    manager_approved_at = Column(DateTime(timezone=True), nullable=True)
    hr_approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    hr_approved_at = Column(DateTime(timezone=True), nullable=True)
    decision_notes = Column(Text, nullable=True)

    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_correction_emp_date", "employee_id", "attendance_date"),
    )
