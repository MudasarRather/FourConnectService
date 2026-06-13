"""HR Night Shift Policy — per-shift night-ops config.

The `Shift` model already carries a `night_allowance` boolean and the canonical
`shift_type=NIGHT`. This adds the operational detail the night console needs:
allowance amount, OT differential, transport and meal eligibility — keyed 1:1
to a shift.
"""
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Numeric, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class NightShiftPolicy(Base):
    __tablename__ = "hr_night_shift_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    shift_id = Column(UUID(as_uuid=True), ForeignKey("hr_shifts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    allowance_amount = Column(Numeric(10, 2), nullable=False, default=0)
    overtime_rate = Column(Numeric(4, 2), nullable=False, default=1.5)
    transport_required = Column(Boolean, nullable=False, default=False)
    meal_eligible = Column(Boolean, nullable=False, default=False)
    safety_compliance = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    shift = relationship("Shift", foreign_keys=[shift_id])
