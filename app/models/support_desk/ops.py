"""Support Desk — Announcements, Automation Rules, Settings (key/value)."""
import uuid

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Integer, ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.support_desk.constants import AnnouncementAudience


class SdAnnouncement(Base):
    __tablename__ = "support_announcements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    title = Column(String(300), nullable=False)
    category = Column(String(80), nullable=True)
    description = Column(Text, nullable=True)
    publish_date = Column(DateTime(timezone=True), nullable=True)
    expiry_date = Column(DateTime(timezone=True), nullable=True)
    audience = Column(String(20), nullable=False, default=AnnouncementAudience.ALL.value, index=True)
    target_org_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=True)
    target_contract_id = Column(UUID(as_uuid=True), ForeignKey("support_contracts.id"), nullable=True)
    target_user_ids = Column(JSONB, nullable=False, default=list)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SdAutomationRule(Base):
    """Condition → action rule, evaluated by ``app.utils.support_desk.rules``.

    ``conditions`` shape: [{"field":"priority","op":"eq","value":"critical"}, ...]
    ``actions`` shape: [{"type":"route_queue","value":"<queue_uuid>"}, ...]

    ``trigger``: 'on_create' rules run first-match at ticket creation (before the
    category/type fallback router); 'time_based' rules double as auto-escalation
    policies — swept against open tickets whose age exceeds ``time_threshold_mins``.
    ``stop_processing``: True = first-match semantics; False = fall-through so later
    rules may still fire. New columns land via ``add_support_queue_engine_columns.py``.
    """
    __tablename__ = "support_automation_rules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    name = Column(String(160), nullable=False)
    description = Column(Text, nullable=True)
    match_type = Column(String(8), nullable=False, default="all")  # all | any
    conditions = Column(JSONB, nullable=False, default=list)
    actions = Column(JSONB, nullable=False, default=list)
    order_index = Column(Integer, nullable=False, default=0)
    trigger = Column(String(16), nullable=False, default="on_create")  # on_create | time_based
    stop_processing = Column(Boolean, nullable=False, default=True)
    time_threshold_mins = Column(Integer, nullable=True)               # time_based: minimum open age
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    run_count = Column(Integer, nullable=False, default=0)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SdRuleRevision(Base):
    """One row per routing-rule change (ServiceNow-style config versioning).

    ``snapshot`` is the rule's FULL state AFTER the change (``action`` says what
    happened); ``version`` counts up per rule. NEW table — auto-created by
    ``Base.metadata.create_all()``. Powers the Queue Config "Ledger" panel's
    per-rule history + diff view.
    """
    __tablename__ = "support_rule_revisions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    rule_id = Column(UUID(as_uuid=True), ForeignKey("support_automation_rules.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    action = Column(String(16), nullable=False, default="updated")  # created | updated | deleted
    snapshot = Column(JSONB, nullable=False, default=dict)
    changed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    def __repr__(self):
        return f"<SdRuleRevision {self.rule_id} v{self.version} {self.action}>"


class SdSetting(Base):
    """Singleton-ish key/value store for module settings (numbering ref, email,
    SLA defaults, portal branding, CSAT config)."""
    __tablename__ = "support_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    key = Column(String(80), nullable=False, unique=True, index=True)
    value = Column(JSONB, nullable=False, default=dict)
    updated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
