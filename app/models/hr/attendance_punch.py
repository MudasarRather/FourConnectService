"""Raw attendance punches — append-only log.

Every check-in / check-out / break-start / break-end flows through this table.
The router for this resource is READ + INSERT only; no UPDATE / DELETE is
exposed. `daily_rollup` links punches to their owning Attendance row via
`attendance_id` after computing the per-day rollup.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Index, Numeric,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.hr.attendance import AttendanceSource


class PunchType(str, enum.Enum):
    IN = "IN"
    OUT = "OUT"
    BREAK_START = "BREAK_START"
    BREAK_END = "BREAK_END"


class AttendancePunch(Base):
    """Append-only punch event. No update / delete."""
    __tablename__ = "hr_attendance_punches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    attendance_id = Column(UUID(as_uuid=True), ForeignKey("hr_attendance.id", ondelete="SET NULL"), nullable=True, index=True)

    punch_time = Column(DateTime(timezone=True), nullable=False, index=True)
    punch_type = Column(Enum(PunchType, name="hr_punch_type"), nullable=False)
    source = Column(Enum(AttendanceSource, name="hr_attendance_source"), nullable=False, default=AttendanceSource.WEB)

    device_id = Column(String(120), nullable=True)
    geo_lat = Column(Numeric(10, 7), nullable=True)
    geo_lng = Column(Numeric(10, 7), nullable=True)
    geo_verified = Column(Boolean, nullable=False, default=False)
    geo_distance_m = Column(Numeric(10, 2), nullable=True)
    selfie_url = Column(String(500), nullable=True)
    payload = Column(JSONB, nullable=False, default=dict)  # raw vendor payload for biometric

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    employee = relationship("Employee", foreign_keys=[employee_id])

    __table_args__ = (
        Index("ix_hr_punch_emp_time", "employee_id", "punch_time"),
    )
