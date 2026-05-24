"""HR Asset inventory + allocation models.

Tracks company-owned assets (laptops, mobiles, RFID cards, vehicles, keys) and
their allocation history to employees. Designed to outlive a single onboarding —
an asset is allocated during onboarding then re-allocated later as employees
transfer / exit / equipment is refreshed.
"""
import enum
import uuid
from datetime import date
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Date,
    Enum, Integer, Text, Index, Numeric,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class AssetType(str, enum.Enum):
    LAPTOP = "LAPTOP"
    DESKTOP = "DESKTOP"
    MOBILE = "MOBILE"
    SIM = "SIM"
    RFID_CARD = "RFID_CARD"
    ID_CARD = "ID_CARD"
    HEADSET = "HEADSET"
    MONITOR = "MONITOR"
    KEYBOARD = "KEYBOARD"
    MOUSE = "MOUSE"
    VEHICLE = "VEHICLE"
    KEYS = "KEYS"
    OTHER = "OTHER"


class AssetCondition(str, enum.Enum):
    NEW = "NEW"
    GOOD = "GOOD"
    FAIR = "FAIR"
    POOR = "POOR"
    RETIRED = "RETIRED"


class AssetStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    ALLOCATED = "ALLOCATED"
    RESERVED = "RESERVED"
    MAINTENANCE = "MAINTENANCE"
    RETIRED = "RETIRED"


class AllocationStatus(str, enum.Enum):
    ALLOCATED = "ALLOCATED"
    RETURNED = "RETURNED"
    LOST = "LOST"
    DAMAGED = "DAMAGED"


class Asset(Base):
    """Master asset inventory row."""
    __tablename__ = "hr_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_code = Column(String(60), unique=True, nullable=False, index=True)  # e.g. LAP-001
    asset_type = Column(Enum(AssetType, name="hr_asset_type"), nullable=False, index=True)
    brand = Column(String(120), nullable=True)
    model = Column(String(160), nullable=True)
    serial_number = Column(String(120), nullable=True, index=True)
    purchase_date = Column(Date, nullable=True)
    purchase_cost = Column(Numeric(12, 2), nullable=True)
    condition = Column(Enum(AssetCondition, name="hr_asset_condition"), nullable=False, default=AssetCondition.NEW)
    status = Column(Enum(AssetStatus, name="hr_asset_status"), nullable=False, default=AssetStatus.AVAILABLE, index=True)
    assigned_employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id"), nullable=True, index=True)
    location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id"), nullable=True)
    notes = Column(Text, nullable=True)
    extra = Column(JSONB, default=dict, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    assigned_employee = relationship("Employee", foreign_keys=[assigned_employee_id])

    __table_args__ = (
        Index("ix_hr_assets_type_status", "asset_type", "status"),
    )


class AssetAllocation(Base):
    """History of asset → employee allocations."""
    __tablename__ = "hr_asset_allocations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    asset_id = Column(UUID(as_uuid=True), ForeignKey("hr_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    employee_id = Column(UUID(as_uuid=True), ForeignKey("hr_employees.id", ondelete="CASCADE"), nullable=False, index=True)
    process_id = Column(UUID(as_uuid=True), ForeignKey("hr_onboarding_processes.id", ondelete="SET NULL"), nullable=True, index=True)

    allocated_date = Column(Date, nullable=False, default=date.today, server_default=func.current_date())
    expected_return_date = Column(Date, nullable=True)
    returned_date = Column(Date, nullable=True)
    condition_on_issue = Column(Enum(AssetCondition, name="hr_asset_condition"), nullable=True)
    condition_on_return = Column(Enum(AssetCondition, name="hr_asset_condition"), nullable=True)
    status = Column(Enum(AllocationStatus, name="hr_asset_allocation_status"), nullable=False, default=AllocationStatus.ALLOCATED, index=True)
    acknowledged_by_employee = Column(Boolean, default=False, nullable=False)
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    issued_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    returned_to_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    asset = relationship("Asset", foreign_keys=[asset_id])
    employee = relationship("Employee", foreign_keys=[employee_id])
