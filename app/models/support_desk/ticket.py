"""Support Desk — the Ticket and its conversation/timeline children.

A ticket can be raised by an external client (organization + customer, via the
public portal) OR internally by an employee (``raised_by_user_id``, the user
self-service surface). ``public_token`` powers the no-auth client portal exactly
like the exit-document portal.
"""
import uuid

from sqlalchemy import (
    Column, String, Text, Boolean, DateTime, Integer, BigInteger, ForeignKey, Index,
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
    subcategory_id = Column(UUID(as_uuid=True), ForeignKey("support_categories.id"), nullable=True, index=True)
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
    team_id = Column(UUID(as_uuid=True), ForeignKey("support_teams.id"), nullable=True, index=True)
    queue_id = Column(UUID(as_uuid=True), ForeignKey("support_queues.id"), nullable=True, index=True)
    assigned_agent_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    assigned_engineer_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    assigned_pm_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # Additional people who can see + work this ticket (beyond the single owner). [user_id, ...]
    collaborators = Column(JSONB, nullable=False, default=list)

    # ── SLA ──
    sla_package_id = Column(UUID(as_uuid=True), ForeignKey("support_sla_packages.id"), nullable=True)
    response_due_at = Column(DateTime(timezone=True), nullable=True)
    resolution_due_at = Column(DateTime(timezone=True), nullable=True)
    first_responded_at = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    closed_at = Column(DateTime(timezone=True), nullable=True)
    sla_response_breached = Column(Boolean, nullable=False, default=False, index=True)
    sla_resolution_breached = Column(Boolean, nullable=False, default=False, index=True)
    # Breach-detection stamps (Breached desk / "Time-Debt Meter"): when each target was
    # missed. Stamped with the DUE instant (not detection time) by recompute_breach_flags
    # and the breach sweep — powers breach aging + sort-by-overage.
    sla_response_breached_at = Column(DateTime(timezone=True), nullable=True, index=True)
    sla_resolution_breached_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # ── SLA stop-the-clock ── (see SLA_PAUSE_STATUSES)
    # sla_paused_since: when the clock is currently frozen (NULL = running). While set, SLA
    #   state is evaluated as of this instant. sla_paused_ms: total time banked across pauses
    #   (for display / reporting); on resume the deadlines above are shifted out by the pause.
    sla_paused_since = Column(DateTime(timezone=True), nullable=True)
    sla_paused_ms = Column(BigInteger, nullable=False, default=0, server_default="0")

    # ── Escalation ──
    is_escalated = Column(Boolean, nullable=False, default=False, index=True)
    escalation_level = Column(Integer, nullable=False, default=0)
    escalated_at = Column(DateTime(timezone=True), nullable=True)
    escalation_reason = Column(Text, nullable=True)
    # Structured escalation record ("Thermal Updraft" desk): who raised it, which direction
    # (hierarchical|functional), the coded reason, and the real target team when functional.
    escalation_type = Column(String(20), nullable=True)
    escalation_reason_code = Column(String(40), nullable=True)
    escalated_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    escalated_to_team_id = Column(UUID(as_uuid=True), ForeignKey("support_teams.id"), nullable=True)
    # Escalation ACK — "the receiving tier owns eyes on this". DISTINCT from the war-room
    # acknowledged_at (incident MTTA): this drives eMTTA and is CLEARED on every level bump
    # so each new tier demands a fresh acknowledgement.
    escalation_acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    escalation_acknowledged_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # Deadline for the receiving tier to ACK (per-priority default; per-escalation override).
    # Deliberately NOT cleared on ack — overdue is computed as unacked AND past-due.
    escalation_response_due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    # Set once by the SLA-breach sweep so auto-escalation fires exactly once per ticket
    # (mirrors vendor_overdue_flagged) + powers the "auto-escalated" chip.
    auto_escalated_at = Column(DateTime(timezone=True), nullable=True)
    # ── Reopen lifecycle ("Möbius Loop" desk) ──
    # reopened_count/reopen_reason predate this block; the rest make each reopen a real
    # record: the coded verdict on the failed fix (ReopenReason taxonomy), WHO kicked it
    # back and HOW (requester|agent|portal|auto), when, the resolved→reopen gap of the
    # LAST cycle (time-to-reopen), and a snapshot of the failed resolution — preserved
    # here because the live resolution fields are cleared for the fresh cycle.
    # Written ONLY by _common.apply_reopen (single-writer rule).
    reopened_count = Column(Integer, nullable=False, default=0, index=True)
    reopen_reason = Column(Text, nullable=True)
    reopen_reason_code = Column(String(40), nullable=True)
    reopen_source = Column(String(20), nullable=True)      # requester|agent|portal|auto
    last_reopened_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_reopened_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reopen_latency_ms = Column(BigInteger, nullable=True)
    prev_resolution_code = Column(String(40), nullable=True)
    prev_resolution_summary = Column(Text, nullable=True)
    prev_resolved_at = Column(DateTime(timezone=True), nullable=True)

    # ── On-hold (Phase 2 + Suspension Dock) ──
    hold_reason = Column(String(240), nullable=True)
    hold_until = Column(DateTime(timezone=True), nullable=True)
    held_at = Column(DateTime(timezone=True), nullable=True)
    held_from_status = Column(String(30), nullable=True)  # status to restore on resume
    # Coded hold-reason category (HoldReason taxonomy) — hold_reason stays the free-text
    # detail. Drives the reason-composition analytics on the On-Hold board.
    hold_reason_code = Column(String(40), nullable=True)
    # Hold-review governance: extending/re-confirming a hold stamps a review. A hold that
    # sails past STALE_HOLD_DAYS without one is flagged stale by the enrichment layer.
    last_hold_review_at = Column(DateTime(timezone=True), nullable=True)
    hold_review_count = Column(Integer, nullable=False, default=0, server_default="0")

    # ── Pending-customer reminders (Phase 2) ──
    last_customer_reply_at = Column(DateTime(timezone=True), nullable=True)
    reminder_count = Column(Integer, nullable=False, default=0)
    last_reminder_at = Column(DateTime(timezone=True), nullable=True)

    # ── Pending-vendor (Phase 2 memos) ──
    vendor_name = Column(String(160), nullable=True)
    vendor_ticket_ref = Column(String(120), nullable=True)
    vendor_status = Column(String(60), nullable=True)
    # ── Pending-vendor lifecycle (Vendor Relay Station) ──
    # The desk's third-party hand-off record. vendor_dispatched_at is stamped once when a
    # ticket first enters PENDING_VENDOR; vendor_due_at is the vendor OLA/ETA (when we expect
    # a reply — DISTINCT from the customer SLA, which stays paused the whole time);
    # vendor_reply_at is stamped when the vendor responds / we bring the ticket back.
    # vendor_reminder_count + last_vendor_reminder_at track chase follow-ups (mirrors the
    # pending-customer reminder pair but is INTERNAL — never notifies the client).
    vendor_dispatched_at = Column(DateTime(timezone=True), nullable=True)
    vendor_due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    vendor_reply_at = Column(DateTime(timezone=True), nullable=True)
    vendor_reminder_count = Column(Integer, nullable=False, default=0, server_default="0")
    last_vendor_reminder_at = Column(DateTime(timezone=True), nullable=True)
    vendor_wait_reason = Column(String(40), nullable=True)   # awaiting_quote|awaiting_parts|awaiting_rma|awaiting_fix|awaiting_approval|awaiting_info|other
    vendor_po_ref = Column(String(120), nullable=True)       # optional PO / cost reference (procurement seam)
    # Set once by the vendor-overdue sweep so auto-escalation fires exactly once per hand-off.
    vendor_overdue_flagged = Column(Boolean, nullable=False, default=False, server_default="false")

    # ── Critical / major-incident + business impact (Phase 2) ──
    is_major_incident = Column(Boolean, nullable=False, default=False, index=True)
    business_impact = Column(String(20), nullable=True)        # low|medium|high|critical
    affected_users = Column(Integer, nullable=True)
    revenue_impact = Column(String(160), nullable=True)
    war_room_url = Column(String(400), nullable=True)

    # ── War Room (ACK + stakeholder update cadence) ──
    # acknowledged_at/by = ServiceNow-style acknowledge (MTTA source). DISTINCT from
    # first_responded_at, which is the customer-facing first reply and drives the SLA.
    acknowledged_at = Column(DateTime(timezone=True), nullable=True)
    acknowledged_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # Stakeholder status-update cadence: while armed, next_update_due_at counts down to the
    # next promised update; posting a status update re-arms it. The update-overdue sweep
    # nudges the owner when it lapses. Armed on major-incident declare or manually.
    update_interval_minutes = Column(Integer, nullable=True)
    next_update_due_at = Column(DateTime(timezone=True), nullable=True, index=True)
    last_status_update_at = Column(DateTime(timezone=True), nullable=True)

    # ── Incident command (Fault Grid / Command Funnel desks) ──
    # MI response roster (ServiceNow MIM / PagerDuty response-roles parity): the commander
    # owns the response, the comms lead owns stakeholder updates, the ops lead owns the
    # technical bridge. Distinct from assigned_agent_id — the assignee keeps working the
    # ticket; the roster coordinates the RESPONSE. Written only by /incident-roles.
    incident_commander_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    comms_lead_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    ops_lead_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    # Impact detail: which named services/systems are hit ([str] — free service labels or
    # SdServiceItem names), when the disruption actually STARTED vs when it was DETECTED
    # (both can predate created_at — monitoring lag is real), and the compliance/security/
    # public exposure flags the SEV1/SEV2 desk surfaces (revenue_impact already exists above).
    affected_services = Column(JSONB, nullable=False, default=list)
    incident_started_at = Column(DateTime(timezone=True), nullable=True)
    incident_detected_at = Column(DateTime(timezone=True), nullable=True)
    compliance_impact = Column(Boolean, nullable=False, default=False, server_default="false")
    security_impact = Column(Boolean, nullable=False, default=False, server_default="false")
    public_impact = Column(Boolean, nullable=False, default=False, server_default="false")
    # Parent/child linking (ServiceNow child-incident parity, ONE level deep): children
    # roll up under a master incident. Distinct from merged_into_id (dedup tombstone) and
    # linked_problem_id (ITIL problem) — a child stays a live, separately-worked ticket.
    # Written only by /incident-parent.
    parent_incident_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"),
                                nullable=True, index=True)
    # MI-candidate proposal (ServiceNow "major incident candidate" parity): an owner-tier
    # agent PROPOSES major status; a team lead / superuser confirms (→ is_major_incident)
    # or declines with a note. Direct declare is lead/superuser-only. Stamps clear on
    # confirm/decline/direct-declare; history lives in mi_proposed/mi_confirmed/mi_declined
    # activity rows. mi_proposed_by_id is a bare UUID (no FK) per _INCIDENT_COLUMNS convention.
    mi_proposed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    mi_proposed_by_id = Column(UUID(as_uuid=True), nullable=True)
    mi_proposal_note = Column(String(500), nullable=True)

    # ── SLA-breach root-cause analysis (Phase 2) ──
    breach_reason = Column(String(240), nullable=True)
    rca_summary = Column(Text, nullable=True)
    rca_corrective = Column(Text, nullable=True)
    rca_preventive = Column(Text, nullable=True)
    # ── RCA v2 (RCA desks): structured capture + review workflow ──
    # rca_status = filed|validated|returned|stale (NULL = no RCA). Legacy rows with
    # rca_summary but NULL status READ as 'filed' — always go through
    # utils/support_desk/rca.rca_effective_status(_expr), never the raw column.
    # rca_category mirrors the PIR RootCauseCategory taxonomy at ticket level.
    # rca_five_whys ([str] ≤5) / rca_factors ([str] ≤10) = structured methodology.
    # filed/reviewed stamps are bare UUIDs per the incident-column convention.
    # rca_inherited_from_problem_id = provenance when a problem cascade stamped it.
    rca_status = Column(String(20), nullable=True, index=True)
    rca_category = Column(String(40), nullable=True)
    rca_five_whys = Column(JSONB, nullable=True)
    rca_factors = Column(JSONB, nullable=True)
    rca_filed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    rca_filed_by_id = Column(UUID(as_uuid=True), nullable=True)
    rca_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    rca_reviewed_by_id = Column(UUID(as_uuid=True), nullable=True)
    rca_review_note = Column(String(500), nullable=True)
    rca_inherited_from_problem_id = Column(UUID(as_uuid=True), nullable=True)

    # ── Agent workbench (ITIL triage + resolve + merge + time) ──
    sub_status = Column(String(40), nullable=True)               # finer state within a status
    impact = Column(String(20), nullable=True)                   # low|medium|high|critical
    urgency = Column(String(20), nullable=True)                  # low|medium|high|critical
    resolution_code = Column(String(40), nullable=True)          # solved|workaround|no_fault_found|...
    resolution_summary = Column(Text, nullable=True)
    resolution_category = Column(String(40), nullable=True)      # hardware|software|network|user_error|vendor|configuration|other
    # Resolution attribution (Resolved desk / leaderboard): WHO recorded the fix / the close.
    # NULL = system (auto-resolve / auto-close sweeps). Cleared by apply_reopen for the fresh
    # cycle (the failed fix's resolver stays recoverable from the activity trail).
    resolved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    closed_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    time_spent_minutes = Column(Integer, nullable=False, default=0)
    merged_into_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=True)
    # Follow-up linkage (Closed desk): this ticket continues a sealed terminal ticket.
    # Requesters can't reopen CLOSED records — a linked follow-up is the sanctioned path.
    follow_up_of_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"), nullable=True, index=True)
    # Template provenance (Template Studio): the template this ticket was born from.
    template_id = Column(UUID(as_uuid=True), ForeignKey("support_ticket_templates.id"), nullable=True, index=True)
    last_viewed_at = Column(DateTime(timezone=True), nullable=True)

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
    # ── Archive provenance ("Deep Storage" desk). Archive = is_deleted flip; these stamp
    #    WHO/WHEN/WHY. archived_by_id NULL while is_deleted = System (auto_retention sweep).
    #    legal_hold exempts a record from the retention sweep AND from purge eligibility;
    #    any agent may place a hold, only a superuser may release it. Restore clears all four.
    archived_at = Column(DateTime(timezone=True), nullable=True, index=True)
    archived_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    archive_reason_code = Column(String(40), nullable=True)
    legal_hold = Column(Boolean, nullable=False, default=False, server_default="false")
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

    # Redaction (Zendesk/ServiceNow parity): a superuser can scrub sensitive content from a
    # comment. The body is destructively overwritten with a tombstone; who/when/why are kept
    # for the audit trail, but the original text is unrecoverable. A tombstone stays in the
    # thread so the conversation history is honest ("a message was here, then redacted").
    is_redacted = Column(Boolean, nullable=False, default=False)
    redacted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    redacted_at = Column(DateTime(timezone=True), nullable=True)
    redacted_reason = Column(String(300), nullable=True)

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

    # Timeline milestone pin (Incident Timeline desks): a commander/owner-tier actor
    # curates the key beats of an incident. Pins are audit-logged (write_audit), never
    # re-logged as activities — a pin must not spam the very feed it curates.
    is_milestone = Column(Boolean, nullable=False, default=False, server_default="false")
    pinned_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    pinned_at = Column(DateTime(timezone=True), nullable=True)

    ticket = relationship("SdTicket", back_populates="activities")

    def __repr__(self):
        return f"<SdTicketActivity {self.action} on {self.ticket_id}>"


class SdTicketReminder(Base):
    """Personal follow-up pin ("remind me about this ticket on ...") — the Chrono Desk's
    scheduling primitive. Owner-private: a reminder belongs to exactly one user and never
    leaks into another agent's calendar. Deliberately NOT an SLA column — pins carry no
    workflow authority; they only surface as `kind='reminder'` events on the calendar feed."""
    __tablename__ = "support_ticket_reminders"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    remind_at = Column(DateTime(timezone=True), nullable=False, index=True)
    note = Column(String(300), nullable=True)
    done = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    ticket = relationship("SdTicket")

    __table_args__ = (
        Index("ix_support_ticket_reminders_user_at", "user_id", "remind_at"),
    )

    def __repr__(self):
        return f"<SdTicketReminder {self.ticket_id} @ {self.remind_at}>"
