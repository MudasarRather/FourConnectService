"""HR Training & Development — Audit log.

Immutable event trail for the whole module (programs, assignments, certifications,
skills, requests, trainers, materials, feedback, compliance). Clones the shape of
``ClaimAuditLog`` so the frontend audit-log table renders identically.

New table — auto-created on startup via ``Base.metadata.create_all``.
"""
import enum
import uuid

from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Enum, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class TrainingAuditAction(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    ASSIGN = "ASSIGN"
    SUBMIT = "SUBMIT"
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    RETURN = "RETURN"
    CANCEL = "CANCEL"
    FULFILL = "FULFILL"
    COMPLETE = "COMPLETE"
    WAIVE = "WAIVE"
    FAIL = "FAIL"
    RENEW = "RENEW"
    REASSIGN = "REASSIGN"
    EXPIRE = "EXPIRE"
    FEEDBACK = "FEEDBACK"


class TrainingAuditLog(Base):
    __tablename__ = "hr_training_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # PROGRAM | ASSIGNMENT | CERTIFICATION | SKILL | REQUEST | TRAINER | MATERIAL |
    # FEEDBACK | COMPLIANCE
    entity_type = Column(String(40), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    action = Column(Enum(TrainingAuditAction, name="hr_training_audit_action"), nullable=False, index=True)

    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=True)
    payload = Column(JSONB, nullable=True)
    note = Column(String(300), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_hr_training_audit_entity", "entity_type", "entity_id"),
    )

    def __repr__(self):
        return f"<TrainingAuditLog {self.action} {self.entity_type}>"
