"""Support Desk — L2 workbench collaboration entities: Worklog, Watcher, Swarm.

New tables only (auto-created by ``Base.metadata.create_all()`` on startup) —
nothing here alters existing tables, so no ad-hoc migration script is needed.

ServiceNow/Zendesk parity notes:
  • ``SdTicketWorklog``  — per-entry effort record (ServiceNow work notes). The legacy
    cumulative ``SdTicket.time_spent_minutes`` counter stays the single number every
    existing surface reads; the router keeps it in sync (increment on create,
    decrement on soft-delete).
  • ``SdTicketWatcher``  — a true self-service subscription (Zendesk followers),
    distinct from ``SdTicket.collaborators`` which grants ACT rights. Watchers only
    get notified (status change / tier move / resolution).
  • ``SdSwarmSession``   — a formal swarm record (ServiceNow swarming): at most ONE
    active session per ticket; joining also adds the agent to the ticket's
    ``collaborators`` so the existing owner-tier gates grant them act rights.
"""
import uuid

from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func

from app.database import Base


class SdTicketWorklog(Base):
    """One logged slice of effort on a ticket — who, how long, what kind, when."""
    __tablename__ = "support_ticket_worklogs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    minutes = Column(Integer, nullable=False)
    note = Column(Text, nullable=True)
    work_type = Column(String(24), nullable=False, default="work")  # work|diagnosis|research|comms|handoff
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<SdTicketWorklog {self.ticket_id}:{self.minutes}m>"


class SdTicketWatcher(Base):
    """A follow subscription — the unique pair makes watch/unwatch naturally idempotent."""
    __tablename__ = "support_ticket_watchers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("ticket_id", "user_id", name="uq_support_ticket_watchers_tu"),)

    def __repr__(self):
        return f"<SdTicketWatcher {self.ticket_id}:{self.user_id}>"


class SdSwarmSession(Base):
    """A swarm — a bounded burst of multi-agent collaboration on one hard ticket."""
    __tablename__ = "support_swarm_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=False, index=True)
    started_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    participant_ids = Column(JSONB, nullable=False, default=list)   # [user_id, ...] (JSONB — mutate via flag_modified)
    status = Column(String(12), nullable=False, default="active", index=True)  # active | ended
    outcome = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    ended_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    def __repr__(self):
        return f"<SdSwarmSession {self.ticket_id}:{self.status}>"
