"""Geo-fences — SKELETON for Phase 2.X.

Lat/lng centroid + radius. `verify_geofence()` in
`app.utils.hr.attendance_logic` returns verified=True when zero fences exist
for the employee's `work_location_id` (graceful default), otherwise haversine
distance is checked against every active fence.
"""
import uuid
from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey,
    Integer, Numeric, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from app.database import Base


class GeoFence(Base):
    __tablename__ = "hr_geo_fences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    location_id = Column(UUID(as_uuid=True), ForeignKey("hr_work_locations.id", ondelete="SET NULL"), nullable=True, index=True)
    center_lat = Column(Numeric(10, 7), nullable=False)
    center_lng = Column(Numeric(10, 7), nullable=False)
    radius_meters = Column(Integer, nullable=False, default=200)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
