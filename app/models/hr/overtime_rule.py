"""HR Overtime Rules — the config layer that scores OvertimeRequest hours.

Defines thresholds, multipliers and caps per OT type (daily / weekly / holiday
/ night / emergency). Reuses the existing ``OtType`` enum from
``app.models.hr.overtime`` — the ``hr_ot_type`` PG enum already exists, so the
column MUST pass ``create_type=False`` (otherwise create_all tries to re-create
the type and errors).
"""
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Enum, Numeric, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database import Base
from app.models.hr.overtime import OtType


class OvertimeRule(Base):
    __tablename__ = "hr_overtime_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(120), nullable=False)
    ot_type = Column(Enum(OtType, name="hr_ot_type", create_type=False), nullable=False, default=OtType.WEEKDAY)
    # Hours worked beyond this (per the rule's window) qualify as OT.
    threshold_hours = Column(Numeric(5, 2), nullable=False, default=8.0)
    # Pay multiplier on the base hourly rate (1.5 = time-and-a-half, 2.0 = double).
    multiplier = Column(Numeric(4, 2), nullable=False, default=1.5)
    # Cap on payable OT hours per occurrence (NULL = uncapped).
    max_ot_hours = Column(Numeric(5, 2), nullable=True)
    approval_required = Column(Boolean, nullable=False, default=True)
    # Departments this rule applies to ([] = all).
    department_ids = Column(JSONB, nullable=False, default=list)
    priority = Column(Numeric(4, 0), nullable=False, default=0)  # higher wins when multiple match
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    description = Column(String(400), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        Index("ix_hr_ot_rule_type_active", "ot_type", "is_active"),
    )
