"""Support Desk — the Ticket and its conversation/timeline children.

A ticket can be raised by an external client (organization + customer, via the
public portal) OR internally by an employee (``raised_by_user_id``, the user
self-service surface). ``public_token`` powers the no-auth client portal exactly
like the exit-document portal.
"""
import uuid

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Integer, ForeignKey, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, TicketType, TicketSource, CommentAuthorKind,
)


class SdTicket(Base):
    __tablename__ = "support_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_number = Column(String(40), nullable=False, unique=True, index=True)

    # ── Ticket information ──
    subject = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("support_categories.id"), nullable=True, index=True)
    ticket_type = Column(String(30), nullable=False, default=TicketType.INCIDENT.value)
    priority = Column(String(20), nullable=False, default=TicketPriority.MEDIUM.value, index=True)
    source = Column(String(20), nullable=False, default=TicketSource.INTERNAL.value)
    status = Column(String(30), nullable=False, default=TicketStatus.OPEN.value, index=True)

    # ── Customer information (external) ──
    organization_id = Column(UUID(as_uuid=True), ForeignKey("support_organizations.id"), nullable=True, index=True)
    customer_id = Column(UUID(as_uuid=True), ForeignKey("support_customers.id"), nullable=True, index=True)
    contact_name = Column(String(160), nullable=True)
    contact_email = Column(String(200), nullable=True, index=True)
    contact_phone = Column(String(40), nullable=True)
    department = Column(String(120), nullable=True)
    location = Column(String(120), nullable=True)

    # ── Internal origin (employee self-service) ──
    is_internal = Column(Boolean, nullable=False, default=False, index=True)
    raised_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)

    # ── Assignment ──
    support_team = Column(String(80), nullable=True)
    assigned_agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    assigned_engineer_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_pm_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # ── SLA ──
    sla_package_id = Column(UUID(as_uuid=True), ForeignKey("support_sla_packages.id"), nullable=True)
    response_due_at = Column(DateTime(timezone=True), nullable=True)
    resolution_due_at = Column(DateTime(timezone=True), nullable=True)
    first_responded_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    sla_response_breached = Column(Boolean, nullable=False, default=False, index=True)
    sla_resolution_breached = Column(Boolean, nullable=False, default=False, index=True)

    # ── Escalation ──
    is_escalated = Column(Boolean, nullable=False, default=False, index=True)
    escalation_level = Column(Integer, nullable=False, default=0)
    escalated_at = Column(DateTime(timezone=True), nullable=True)
    reopened_count = Column(Integer, nullable=False, default=0)

    # ── Linked records (ERP integration seams; loose links in JSONB + explicit FKs) ──
    linked_change_id = Column(UUID(as_uuid=True), ForeignKey("support_change_requests.id"), nullable=True)
    linked_problem_id = Column(UUID(as_uuid=True), ForeignKey("support_problems.id"), nullable=True)
    links = Column(JSONB, nullable=False, default=dict)  # {project_task_id, invoice_id, ...}

    # ── Attachments + tags ──
    attachments = Column(JSONB, nullable=False, default=list)  # [{name,url,size,mime}]
    tags = Column(JSONB, nullable=False, default=list)

    # ── CSAT ──
    csat_score = Column(Integer, nullable=True)   # 1..5
    csat_comment = Column(Text, nullable=True)

    # ── Public portal ──
    public_token = Column(String(64), nullable=True, unique=True, index=True)
    public_token_expires_at = Column(DateTime(timezone=True), nullable=True)

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    comments = relationship(
        "SdTicketComment", back_populates="ticket",
        cascade="all, delete-orphan", order_by="SdTicketComment.created_at",
    )
    activities = relationship(
        "SdTicketActivity", back_populates="ticket",
        cascade="all, delete-orphan", order_by="SdTicketActivity.created_at",
    )

    __table_args__ = (
        Index("ix_support_tickets_status_priority", "status", "priority"),
        Index("ix_support_tickets_assigned_open", "assigned_agent_id", "status"),
    )

    def __repr__(self):
        return f"<SdTicket {self.ticket_number} [{self.status}]>"


class SdTicketComment(Base):
    """A conversation entry — client/agent reply, internal note, or system event."""
    __tablename__ = "support_ticket_comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=False, index=True)
    author_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    author_name = Column(String(160), nullable=True)
    author_kind = Column(String(20), nullable=False, default=CommentAuthorKind.STAFF.value)
    body = Column(Text, nullable=False)
    is_internal = Column(Boolean, nullable=False, default=False, index=True)  # staff-only note
    attachments = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    ticket = relationship("SdTicket", back_populates="comments")

    def __repr__(self):
        return f"<SdTicketComment {self.ticket_id} by {self.author_name}>"


class SdTicketActivity(Base):
    """Immutable timeline entry — created/assigned/status/escalated/resolved/…"""
    __tablename__ = "support_ticket_activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=False, index=True)
    actor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    actor_name = Column(String(160), nullable=True)
    action = Column(String(60), nullable=False)  # created/assigned/status_changed/escalated/...
    detail = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    ticket = relationship("SdTicket", back_populates="activities")

    def __repr__(self):
        return f"<SdTicketActivity {self.action} on {self.ticket_id}>"
