"""HR Travel — Audit log.

Immutable event trail for the whole module (requests, bookings, advances, DA,
settlements, categories, policies, DA rates). Clones the shape of
``ClaimAuditLog`` so the frontend audit-log table renders identically.

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.travel_type import TravelAuditAction


class TravelAuditLog(Base):
    __tablename__ = "hr_travel_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    entity_type = Column(String(40), nullable=False)   # REQUEST | BOOKING | ADVANCE | DA | SETTLEMENT | CATEGORY | POLICY | DA_RATE
    entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(Enum(TravelAuditAction, name="hr_travel_audit_action"), nullable=False, index=True)

    travel_request_id = Column(UUID(as_uuid=True), ForeignKey("hr_travel_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=True)
    payload = Column(JSONB, nullable=True)
    note = Column(String(300), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_hr_travel_audit_entity", "entity_type", "entity_id"),
    )

    def __repr__(self):
        return f"<TravelAuditLog {self.action} {self.entity_type}>"
