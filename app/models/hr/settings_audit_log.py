"""HR Settings — change audit log.

Every Settings write appends a row here via ``log_settings_change``. The unified
Audit Logs screen reads this table AND folds in recent rows from the existing
module audit tables (payroll / exit / travel / claim / training) at read time —
so there's one governance ledger without duplicating or migrating those tables.

New table — auto-created by ``Base.metadata.create_all()`` on startup.
"""
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class SettingsAuditLog(Base):
    __tablename__ = "hr_settings_audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    entity_type = Column(String(50), nullable=False, index=True)   # DEPARTMENT, NUMBERING, PAYROLL_RULE, …
    entity_id = Column(UUID(as_uuid=True), nullable=True)
    action = Column(String(20), nullable=False)                    # CREATE | UPDATE | DELETE
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    before_json = Column(JSONB, nullable=True)
    after_json = Column(JSONB, nullable=True)
    note = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    __table_args__ = (
        Index("ix_hr_settings_audit_entity", "entity_type", "created_at"),
    )

    def __repr__(self):
        return f"<SettingsAuditLog {self.entity_type}/{self.action}>"
