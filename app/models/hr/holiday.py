"""Holiday calendar — SKELETON for Phase 2.X.

Supports per-location holidays via optional `location_id` (NULL = company-wide).
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Text, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base


class HolidayType(str, enum.Enum):
    NATIONAL = "NATIONAL"
    COMPANY = "COMPANY"
    REGIONAL = "REGIONAL"
    RESTRICTED = "RESTRICTED"


class Holiday(Base):
    __tablename__ = "hr_holidays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    date = Column(Date, nullable=False, index=True)
    holiday_type = Column(Enum(HolidayType, name="hr_holiday_type"), nullable=False, default=HolidayType.COMPANY)
    location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id", ondelete="SET NULL"), nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Provenance — distinguish admin-entered rows from rows materialized by the
    # bulk public-holiday importer. Without this, an activated import is
    # indistinguishable from a hand-typed holiday and admins lose track of why
    # a date is exempted from leave. Values:
    #   'manual'      — admin typed/edited it via the Holidays UI
    #   'import:in'   — POST /api/hr/holidays/import?country=IN (curated)
    #   'import:nager'— POST /api/hr/holidays/import?country=XX (Nager.Date API)
    source = Column(String(32), nullable=False, server_default="manual", index=True)
    source_ref = Column(String(120), nullable=True)   # e.g. "IN:2026"
