"""HR Exit Management — Audit log.

Immutable workflow-grained event trail for the whole module (cases, clearance,
interviews, settlements, documents, policies). Clones ``TravelAuditLog`` so the
frontend audit-log table renders identically. Distinct from ``EmployeeHistory``,
which is the legal lifecycle trail (written by the employee lifecycle handlers).

New table — auto-created on startup.
"""
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.hr.exit_type import ExitAuditAction


class ExitAuditLog(Base):
    __tablename__ = "hr_exit_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    entity_type = Column(String(40), nullable=False)   # CASE | CLEARANCE | INTERVIEW | SETTLEMENT | DOCUMENT | POLICY
    entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(Enum(ExitAuditAction, name="hr_exit_audit_action"), nullable=False, index=True)

    exit_case_id = Column(UUID(as_uuid=True), ForeignKey("hr_exit_cases.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=True)
    payload = Column(JSONB, nullable=True)
    note = Column(String(300), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_hr_exit_audit_entity", "entity_type", "entity_id"),
    )

    def __repr__(self):
        return f"<ExitAuditLog {self.action} {self.entity_type}>"
