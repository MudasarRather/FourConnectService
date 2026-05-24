"""HR Induction sessions + attendance.

Sessions are scheduled events (welcome talk, dept orientation, policy briefing,
team intro). Attendance tracks each employee's RSVP/attendance state.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Integer, Text, Index, Numeric,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class InductionType(str, enum.Enum):
    WELCOME = "WELCOME"
    DEPT_ORIENTATION = "DEPT_ORIENTATION"
    POLICY = "POLICY"
    COMPLIANCE = "COMPLIANCE"
    TEAM_INTRO = "TEAM_INTRO"
    SAFETY = "SAFETY"
    OTHER = "OTHER"


class AttendanceStatus(str, enum.Enum):
    INVITED = "INVITED"
    CONFIRMED = "CONFIRMED"
    ATTENDED = "ATTENDED"
    MISSED = "MISSED"
    EXCUSED = "EXCUSED"


class InductionSession(Base):
    __tablename__ = "hr_induction_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(200), nullable=False)
    session_type = Column(Enum(InductionType, name="hr_induction_type"), nullable=False, index=True)
    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes = Column(Integer, nullable=True, default=60)
    location = Column(String(240), nullable=True)
    meeting_url = Column(String(600), nullable=True)
    host_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    capacity = Column(Integer, nullable=True)
    agenda = Column(Text, nullable=True)
    materials_url = Column(String(600), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class InductionAttendance(Base):
    __tablename__ = "hr_induction_attendance"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    session_id = Column(UUID(as_uuid=True), ForeignKey("hr_induction_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    process_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True, index=True)
    status = Column(
        Enum(AttendanceStatus, name="hr_induction_attendance_status"),
        nullable=False, default=AttendanceStatus.INVITED, index=True,
    )
    rating = Column(Numeric(3, 1), nullable=True)
    feedback = Column(Text, nullable=True)
    rsvp_at = Column(DateTime(timezone=True), nullable=True)
    attended_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_hr_induction_attendance_session_emp", "session_id", "employee_id", unique=True),
    )

    session = relationship("InductionSession", foreign_keys=[session_id])
    employee = relationship("Employee", foreign_keys=[employee_id])
