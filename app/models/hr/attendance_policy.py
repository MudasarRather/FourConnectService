"""Attendance Policies — SKELETON for Phase 2.X.

A single model covers OFFICE / WFH / SHIFT / OVERTIME / GRACE / HOLIDAY /
LATE_RULE via a `policy_type` discriminator. The `rules` JSONB is intentionally
free-form so individual policy types can store their own shape; per-type
validators will land alongside the router that actually consumes them.
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database import Base


class PolicyType(str, enum.Enum):
    OFFICE = "OFFICE"
    WFH = "WFH"
    SHIFT = "SHIFT"
    OVERTIME = "OVERTIME"
    GRACE = "GRACE"
    HOLIDAY = "HOLIDAY"
    LATE_RULE = "LATE_RULE"


class AttendancePolicy(Base):
    __tablename__ = "hr_attendance_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    policy_type = Column(Enum(PolicyType, name="hr_policy_type"), nullable=False, index=True)
    description = Column(Text, nullable=True)
    # Examples of `rules` per policy_type:
    #   GRACE         -> {"grace_minutes": 10}
    #   LATE_RULE     -> {"late_threshold_min": 15, "half_day_after_min": 30, "deduct_after_count": 3}
    #   OVERTIME      -> {"min_hours": 1, "max_per_day": 4, "weekend_multiplier": 1.5}
    #   WFH           -> {"max_per_month": 8, "requires_pre_approval": true}
    rules = Column(JSONB, nullable=False, default=dict)
    applicable_department_ids = Column(JSONB, nullable=False, default=list)  # [] = applies to all
    applicable_shift_ids = Column(JSONB, nullable=False, default=list)
    effective_from = Column(Date, nullable=True)
    effective_until = Column(Date, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
