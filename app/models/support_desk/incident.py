"""Support Desk — Incident Management sidecar: the Post-Incident Report (PIR).

Incidents themselves ARE tickets (``SdTicket`` with ``ticket_type='incident'`` /
``is_major_incident`` / SEV derived from priority) — the incident module is a set of
sealed lenses over the ticket table, so the whole status machine, SLA engine, seals
and drawer verbs are inherited, never forked.

The one genuinely-new record is the formal post-incident review document. Before
this table the desk only tracked the GAP (the critical desk's ``missing_rca``
metric); the PIR turns the review into a first-class, approvable, publishable
artifact: exec summary → impacts → timeline snapshot → root cause + five-whys →
corrective/preventive action registers → lessons → approvals → PDF.

One PIR per ticket (unique FK) — creation is idempotent (409 on duplicate).
"""
import uuid

from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.support_desk.constants import PirStatus


class SdIncidentReport(Base):
    __tablename__ = "support_incident_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"),
                       nullable=False, unique=True, index=True)
    report_number = Column(String(40), nullable=False, unique=True, index=True)
    title = Column(String(300), nullable=False)
    status = Column(String(20), nullable=False, default=PirStatus.DRAFT.value, index=True)

    # ── Document sections ──
    executive_summary = Column(Text, nullable=True)
    business_impact = Column(Text, nullable=True)
    technical_impact = Column(Text, nullable=True)
    # Frozen copy of the incident's activity trail at snapshot time: [{at, event, actor, detail}]
    timeline_snapshot = Column(JSONB, nullable=False, default=list)
    root_cause = Column(Text, nullable=True)
    root_cause_category = Column(String(40), nullable=True)   # RootCauseCategory taxonomy
    five_whys = Column(JSONB, nullable=False, default=list)   # up to 5 "why" strings
    # Action registers: [{aid, action, owner_id, owner_name, target_date,
    #                     status(open|in_progress|done)}] — `aid` is the stable
    # per-item address (uuid4 hex[:8], assigned on write) so a draft-era reorder can
    # never re-target a follow-through PATCH; positional index stays as back-compat.
    corrective_actions = Column(JSONB, nullable=False, default=list)
    preventive_actions = Column(JSONB, nullable=False, default=list)
    lessons_learned = Column(Text, nullable=True)

    # ── PIR v2 parity pack (ServiceNow-grade review document) ──
    # Frozen at submit (re-freezable while draft/in_review via PATCH refresh_metrics):
    # {mttd/mtta/mttr/duration_minutes, phase clocks, affected_users, decision/update/
    #  watcher counts, war_room_used, sev} — the PDF and desks read the FROZEN copy so
    # the published record never drifts against live recomputation.
    metrics_snapshot = Column(JSONB, nullable=True)
    contributing_factors = Column(JSONB, nullable=False, default=list)  # ≤10 tag strings
    went_well = Column(JSONB, nullable=False, default=list)             # blameless retro
    went_wrong = Column(JSONB, nullable=False, default=list)            # registers (≤10×300)
    participants = Column(JSONB, nullable=False, default=list)          # [{user_id,name,role}] ≤20
    review_meeting_at = Column(DateTime(timezone=True), nullable=True, index=True)
    review_meeting_notes = Column(Text, nullable=True)
    # Append-on-update audit trail [{at, by_id, by_name, fields}] — capped at 50 (oldest drop)
    revisions = Column(JSONB, nullable=False, default=list)
    # Publish fan-out receipt {at, recipients, watchers, roster, leads}
    distribution = Column(JSONB, nullable=True)

    # ── Review / sign-off trail: [{role, user_id, name, decision(approved|rejected), note, at}] ──
    approvals = Column(JSONB, nullable=False, default=list)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    submitted_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    approved_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    published_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    is_deleted = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    ticket = relationship("SdTicket")

    __table_args__ = (
        Index("ix_support_incident_reports_status_created", "status", "created_at"),
    )

    def __repr__(self):
        return f"<SdIncidentReport {self.report_number} [{self.status}]>"


class SdIncidentTask(Base):
    """One response-playbook task on an incident ticket (ServiceNow incident-task parity).

    Tasks are the check-off layer of a live response: applied in bulk from a curated
    playbook (``INCIDENT_PLAYBOOKS`` — snapshot-on-apply, stamped with ``template_key``)
    or added ad hoc. Statuses: open → done (stamps done_at/by) · open → skipped (the
    tombstone — there is NO delete; skipped keeps the paper trail) · done → open is an
    audited correction. Tasks carry no due dates — the cadence sweep already polices
    response tempo, so there is deliberately no task-overdue cron.
    """
    __tablename__ = "support_incident_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    ticket_id = Column(UUID(as_uuid=True), ForeignKey("support_tickets.id"),
                       nullable=False, index=True)
    seq = Column(Integer, nullable=False, default=0)           # board order (max+10 steps)
    title = Column(String(300), nullable=False)
    note = Column(String(1000), nullable=True)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    status = Column(String(16), nullable=False, default="open", index=True)  # INCIDENT_TASK_STATUSES
    status_note = Column(String(300), nullable=True)           # why skipped / why reopened
    done_at = Column(DateTime(timezone=True), nullable=True)
    done_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    template_key = Column(String(60), nullable=True, index=True)  # provenance (playbook apply)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now(), nullable=False)

    ticket = relationship("SdTicket")

    __table_args__ = (
        Index("ix_support_incident_tasks_ticket_status", "ticket_id", "status"),
    )

    def __repr__(self):
        return f"<SdIncidentTask {self.title[:40]!r} [{self.status}]>"
