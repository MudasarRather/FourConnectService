"""Biometric devices — SKELETON for Phase 2.X.

Tracks the physical devices that push punches into `hr_attendance_punches`.
Vendor-specific sync adapters land in `app.utils.hr.biometric_adapters` (TODO).
"""
import enum
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Enum, Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base


class BiometricDeviceType(str, enum.Enum):
    ZKTECO = "ZKTECO"
    ESSL = "ESSL"
    MATRIX = "MATRIX"
    SUPREMA = "SUPREMA"
    HIKVISION = "HIKVISION"
    OTHER = "OTHER"


class BiometricDeviceStatus(str, enum.Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class BiometricDevice(Base):
    __tablename__ = "hr_biometric_devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    device_id = Column(String(120), unique=True, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    device_type = Column(Enum(BiometricDeviceType, name="hr_biometric_device_type"), nullable=False, default=BiometricDeviceType.ZKTECO)
    location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id", ondelete="SET NULL"), nullable=True, index=True)
    ip_address = Column(String(80), nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    last_sync_status = Column(Enum(BiometricDeviceStatus, name="hr_biometric_device_status"), nullable=False, default=BiometricDeviceStatus.UNKNOWN)
    last_sync_message = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
