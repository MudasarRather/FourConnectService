"""Support Desk — Ticket schemas (admin + self-service + public portal)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─────────────────────────── Comments ───────────────────────────
class CommentCreate(BaseModel):
    body: str
    is_internal: bool = False
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    author_user_id: Optional[UUID] = None
    author_name: Optional[str] = None
    author_kind: str
    body: str
    is_internal: bool
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    created_at: datetime
    is_redacted: bool = False
    redacted_by_id: Optional[UUID] = None
    redacted_at: Optional[datetime] = None
    redacted_reason: Optional[str] = None


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    actor_user_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    action: str
    detail: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ─────────────────────────── Ticket ───────────────────────────
class TicketCreate(BaseModel):
    subject: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    subcategory_id: Optional[UUID] = None
    ticket_type: str = "incident"
    priority: str = "medium"
    impact: Optional[str] = None      # ITIL triage — low|medium|high|critical
    urgency: Optional[str] = None
    source: str = "internal"
    organization_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    support_team: Optional[str] = None
    assigned_agent_id: Optional[UUID] = None
    assigned_engineer_id: Optional[UUID] = None
    assigned_pm_id: Optional[UUID] = None
    sla_package_id: Optional[UUID] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    # Self-service self-claim — honoured only when the requester may WORK the ticket
    # (agent OR member of the team that handles it). Silently ignored otherwise.
    assign_me: bool = False
    # Routing + impact + related records (agent intake). All exist on SdTicket /
    # TicketUpdate already; surfaced on create so the agent console can set them in one
    # shot. team_id is guarded server-side (only manager/admin/team-member may route).
    team_id: Optional[UUID] = None
    queue_id: Optional[UUID] = None
    collaborators: List[UUID] = Field(default_factory=list)
    business_impact: Optional[str] = None
    affected_users: Optional[int] = None
    revenue_impact: Optional[str] = None
    vendor_name: Optional[str] = None
    linked_change_id: Optional[UUID] = None
    linked_problem_id: Optional[UUID] = None
    links: Optional[Dict[str, Any]] = None
    # Template provenance (Template Studio) — set when the intake was prefilled from a
    # template. Unknown/stale ids are silently dropped server-side (never block create).
    template_id: Optional[UUID] = None


class TicketUpdate(BaseModel):
    subject: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    subcategory_id: Optional[UUID] = None
    ticket_type: Optional[str] = None
    priority: Optional[str] = None
    sub_status: Optional[str] = None
    impact: Optional[str] = None
    urgency: Optional[str] = None
    organization_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None
    support_team: Optional[str] = None
    team_id: Optional[UUID] = None
    queue_id: Optional[UUID] = None
    sla_package_id: Optional[UUID] = None
    attachments: Optional[List[Dict[str, Any]]] = None
    tags: Optional[List[str]] = None
    links: Optional[Dict[str, Any]] = None
    # L2 workbench — ITIL record links (incident → problem/change), PATCH-editable so
    # the specialist desk can pin recurring incidents to their problem record.
    linked_problem_id: Optional[UUID] = None
    linked_change_id: Optional[UUID] = None
    # Phase 2 — editable vendor + business-impact fields
    vendor_name: Optional[str] = None
    vendor_ticket_ref: Optional[str] = None
    vendor_status: Optional[str] = None
    # Vendor Relay Station — hand-off lifecycle edits
    vendor_due_at: Optional[datetime] = None
    vendor_wait_reason: Optional[str] = None
    vendor_po_ref: Optional[str] = None
    business_impact: Optional[str] = None
    affected_users: Optional[int] = None
    revenue_impact: Optional[str] = None
    war_room_url: Optional[str] = None


class TicketAssign(BaseModel):
    assigned_agent_id: Optional[UUID] = None
    assigned_engineer_id: Optional[UUID] = None
    assigned_pm_id: Optional[UUID] = None
    support_team: Optional[str] = None
    team_id: Optional[UUID] = None
    queue_id: Optional[UUID] = None


class TicketStatusChange(BaseModel):
    status: str
    note: Optional[str] = None


class TicketCsat(BaseModel):
    csat_score: int = Field(ge=1, le=5)
    csat_comment: Optional[str] = None


class TicketBulkAction(BaseModel):
    """Mass action over a set of tickets. action ∈
    assign | escalate | resolve | close | set_status | set_priority | add_tag.

    Each action carries only the fields it needs; the router guards every ticket
    against the status workflow (assignment-before-work, no direct close, etc.) and
    reports per-ticket which were applied vs skipped — it never blanket-mutates."""
    ids: List[UUID] = Field(min_length=1)
    action: str
    # optional payloads per action
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_agent_id: Optional[UUID] = None
    tag: Optional[str] = None
    note: Optional[str] = None
    # escalate — why it's being raised (kept on the record + posted as an internal note)
    reason: Optional[str] = None
    support_team: Optional[str] = None
    team_id: Optional[UUID] = None   # functional escalation → route the batch to a real team
    # escalate (structured) / de_escalate — coded reason, direction, ack-clock override
    reason_code: Optional[str] = None
    escalation_type: Optional[str] = None
    response_minutes: Optional[int] = Field(default=None, ge=5, le=10080)
    # resolve / close — structured ITIL resolution (mirrors TicketResolve)
    resolution_code: Optional[str] = "solved"
    resolution_category: Optional[str] = None
    resolution_summary: Optional[str] = None
    time_spent_minutes: Optional[int] = Field(default=None, ge=0)
    notify_customer: bool = False
    # vendor bulk ops — vendor_chase | vendor_bring_back | set_vendor_due
    vendor_due_at: Optional[datetime] = None
    message: Optional[str] = None
    # hold / resume bulk ops — park or release a batch (Suspension Dock)
    hold_reason: Optional[str] = None
    hold_reason_code: Optional[str] = None
    hold_until: Optional[datetime] = None
    # legal_hold bulk op (Deep Storage) — True places holds, False releases (superuser only)
    hold: Optional[bool] = None


class TicketBulkResult(BaseModel):
    id: UUID
    ok: bool
    skipped: bool = False           # eligible-but-not-applicable (guard), not an error
    error: Optional[str] = None
    ticket_number: Optional[str] = None


class TicketBulkResponse(BaseModel):
    updated: int
    skipped: int = 0
    results: List[TicketBulkResult]


# ─────────────────────── Phase 2 action bodies ───────────────────────
class TicketRemind(BaseModel):
    message: Optional[str] = None          # custom note appended to the reminder


# ─────────────────────── Deep Storage (Archived desk) action bodies ───────────────────────
class TicketRestore(BaseModel):
    """Un-archive a tombstone. The optional note lands on the 'restored' activity."""
    note: Optional[str] = None


class TicketLegalHold(BaseModel):
    """Place (hold=True) or release (hold=False, superuser only) a legal hold. A held
    record is exempt from the retention sweep and can never become purge-eligible."""
    hold: bool
    note: Optional[str] = None


# ─────────────────────── Vendor Relay Station action bodies ───────────────────────
class TicketVendorDispatch(BaseModel):
    """Hand a ticket off to a third-party vendor: record who/why/ETA and move it into
    PENDING_VENDOR (which auto-pauses the customer SLA). All fields optional so an agent
    can dispatch first and fill details later, but the UI nudges for vendor_name."""
    vendor_name: Optional[str] = None
    vendor_ticket_ref: Optional[str] = None
    vendor_wait_reason: Optional[str] = None    # VendorWaitReason value
    vendor_due_at: Optional[datetime] = None     # vendor OLA / expected-return
    vendor_po_ref: Optional[str] = None
    assigned_engineer_id: Optional[UUID] = None  # internal vendor coordinator
    note: Optional[str] = None                   # internal note logged on the hand-off


class TicketVendorChase(BaseModel):
    """A follow-up nudge TO THE VENDOR (internal tracking only — never notifies the client)."""
    message: Optional[str] = None
    vendor_due_at: Optional[datetime] = None     # optionally push the ETA when re-chasing


class TicketVendorReply(BaseModel):
    """Log a vendor's communication (Zendesk-style side conversation) as an INTERNAL comment,
    and optionally bring the ticket back to IN_PROGRESS (resumes the customer SLA)."""
    body: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    resume: bool = True                          # bring back to in_progress on reply
    vendor_status: Optional[str] = None          # update the external status memo


class TicketRca(BaseModel):
    """RCA v2 capture payload. Every field optional — legacy callers send subsets —
    but the router enforces: no empty strings, summary ≥10 chars when provided,
    category ∈ RootCauseCategory, and ancillary-content-needs-a-summary."""
    breach_reason: Optional[str] = None
    rca_summary: Optional[str] = None
    rca_corrective: Optional[str] = None
    rca_preventive: Optional[str] = None
    rca_category: Optional[str] = None
    rca_five_whys: Optional[List[str]] = Field(default=None, max_length=5)
    rca_factors: Optional[List[str]] = Field(default=None, max_length=10)

    @field_validator("rca_five_whys")
    @classmethod
    def _whys_items(cls, v):
        if v is not None and any(len(str(w)) > 500 for w in v):
            raise ValueError("each why is capped at 500 characters")
        return v

    @field_validator("rca_factors")
    @classmethod
    def _factor_items(cls, v):
        if v is not None and any(len(str(f)) > 240 for f in v):
            raise ValueError("each contributing factor is capped at 240 characters")
        return v


class TicketRcaReview(BaseModel):
    """Lead/superuser ruling on a filed RCA. Note is REQUIRED on return (the router
    422s without it — a returned filing must say what to fix), optional on validate."""
    note: Optional[str] = Field(default=None, max_length=500)


class TicketMajorIncident(BaseModel):
    is_major_incident: bool = True
    business_impact: Optional[str] = None   # low|medium|high|critical
    affected_users: Optional[int] = None
    revenue_impact: Optional[str] = None
    war_room_url: Optional[str] = None
    # Optionally arm the stakeholder status-update cadence on declare (minutes between
    # promised updates). None = leave the cadence as it is.
    update_interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    # Auto-open an L2 swarm session as the war room on declare (reuses a live swarm if
    # one exists) and stamp war_room_url with its deep link when the field is blank.
    # An explicit war_room_url in this payload always wins over the auto-stamp.
    open_war_room: bool = False


# ─────────────────────── War Room action bodies ───────────────────────
class TicketAck(BaseModel):
    """Acknowledge a critical: 'a responder owns eyes on this'. Stamps acknowledged_at/by
    (the MTTA source). Does NOT touch the SLA clocks — first_responded_at stays the
    customer-facing first-reply stamp."""
    note: Optional[str] = None              # optional internal note on the timeline


class TicketStatusUpdate(BaseModel):
    """Post a stakeholder status update (war-room comms). Lands as a ticket comment —
    internal work-note or public reply — and re-arms the update-cadence timer.
    ``phase`` tags the update on the incident-lifecycle track (recorded, optional).
    ``note`` is the stand-down reason — REQUIRED (enforced in the router) when
    ``stop_cadence`` stands down an ARMED cadence; comms never go dark silently."""
    body: str = Field(min_length=2, max_length=4000)
    is_internal: bool = False
    # Re-arm override: minutes until the NEXT promised update. None = keep the current
    # interval (still re-arms from now when an interval is set).
    interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    stop_cadence: bool = False              # stand the update timer down
    phase: Optional[str] = None             # investigating|identified|mitigating|monitoring|resolved
    note: Optional[str] = Field(default=None, max_length=500)
    # Broadcast audience (additive; None = legacy behavior — requester only on public
    # replies). 'stakeholder' additionally fans the update to the ticket's watchers +
    # incident roster; 'internal' just tags the comms log without extra fan-out.
    audience: Optional[str] = None

    @field_validator("phase")
    @classmethod
    def _known_phase(cls, v):
        if v is None or not str(v).strip():
            return None
        v = str(v).strip().lower()
        allowed = {"investigating", "identified", "mitigating", "monitoring", "resolved"}
        if v not in allowed:
            raise ValueError(f"phase must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("audience")
    @classmethod
    def _known_audience(cls, v):
        if v is None or not str(v).strip():
            return None
        v = str(v).strip().lower()
        if v not in ("internal", "stakeholder"):
            raise ValueError("audience must be 'internal' or 'stakeholder'")
        return v


class TicketHold(BaseModel):
    hold_reason: Optional[str] = None
    hold_until: Optional[datetime] = None
    # Coded HoldReason taxonomy value (awaiting_approval | awaiting_change | ... | other).
    # Validated against HOLD_REASON_CODES in the router; hold_reason stays free-text detail.
    hold_reason_code: Optional[str] = None


class TicketResume(BaseModel):
    """Optional resume context — WHY the hold is being lifted ('CAB approved', 'parts
    arrived'). Lands on the timeline as the transition note."""
    reason: Optional[str] = None


class TicketHoldExtend(BaseModel):
    """Review/extend an ACTIVE hold without lifting it: push the release date, re-code the
    reason, leave a note. Stamps last_hold_review_at + hold_review_count (governance)."""
    hold_until: Optional[datetime] = None
    hold_reason_code: Optional[str] = None
    hold_reason: Optional[str] = None
    note: Optional[str] = None


class TicketReopen(BaseModel):
    reason: Optional[str] = None
    # Coded verdict on the failed fix (ReopenReason taxonomy; validated by apply_reopen —
    # an unknown code degrades to None rather than 422ing an otherwise-valid reopen).
    reason_code: Optional[str] = None


class TicketEscalate(BaseModel):
    """Structured escalation context. reason (free text, kept on record + posted as an
    internal note) and legacy support_team stay for compat; the new fields persist the
    full record: type (hierarchical|functional), coded reason, a REAL target team FK for
    functional routing, an ack-clock override, and an optional war-room cadence arm."""
    reason: Optional[str] = None
    support_team: Optional[str] = None
    escalation_type: Optional[str] = None    # hierarchical|functional (router validates)
    reason_code: Optional[str] = None        # EscalationReason taxonomy (router validates)
    team_id: Optional[UUID] = None           # functional escalation → route to this team
    # Minutes the receiving tier has to acknowledge (default = per-priority matrix).
    response_minutes: Optional[int] = Field(default=None, ge=5, le=10080)
    # Optionally arm the stakeholder update cadence in the same action (reuses the
    # war-room update_interval_minutes/next_update_due_at pair — no duplicate fields).
    update_interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)


class TicketEscalationAck(BaseModel):
    """Acknowledge an ESCALATION: 'the receiving tier owns eyes on this'. Stamps
    escalation_acknowledged_at/by (the eMTTA source) — distinct from the war-room ACK."""
    note: Optional[str] = None


class TicketDeEscalate(BaseModel):
    """Stand an escalation down one level. A reason is REQUIRED — de-escalation is a
    judgement call that must be defensible on the timeline."""
    reason: str = Field(min_length=3, max_length=2000)


class TicketResolve(BaseModel):
    """Capture a structured resolution (ITIL): code + root cause + summary + time + proof."""
    resolution_code: str = "solved"
    resolution_category: Optional[str] = None   # hardware|software|network|user_error|vendor|configuration|other
    resolution_summary: Optional[str] = None
    time_spent_minutes: Optional[int] = Field(default=None, ge=0)
    note: Optional[str] = None                  # public reply sent to the requester
    attachments: List[Dict[str, Any]] = Field(default_factory=list)  # proof of resolution
    notify_customer: bool = False
    close: bool = False                         # resolve AND close in one action


class TicketMerge(BaseModel):
    target_id: UUID                             # the surviving "master" ticket
    comment: Optional[str] = None


class CommentRedact(BaseModel):
    """Superuser-only comment redaction (Zendesk/ServiceNow parity). The reason is
    required — a redaction must be defensible on the audit trail. The original body is
    destroyed; only who/when/why survive."""
    reason: str = Field(min_length=3, max_length=300)


class TicketChangeRequester(BaseModel):
    """Superuser-only requester correction — re-home a ticket to the employee who should
    own it (e.g. raised on someone's behalf, or a portal ticket to be internalized).
    Exactly one of the two must be provided."""
    raised_by_user_id: Optional[UUID] = None    # internal employee (users.id)
    reason: Optional[str] = None


class TicketTimeLog(BaseModel):
    minutes: int = Field(ge=1, le=10000)
    note: Optional[str] = None


# ─────────────────────── Self-service (requester + manager) ───────────────────────
class SelfTicketUpdate(BaseModel):
    """Narrow requester edit — only the fields a requester may change, and only
    while the ticket is still OPEN (before an agent engages)."""
    subject: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    category_id: Optional[UUID] = None
    subcategory_id: Optional[UUID] = None
    impact: Optional[str] = None
    urgency: Optional[str] = None
    tags: Optional[List[str]] = None
    attachments: Optional[List[Dict[str, Any]]] = None


class SelfTicketWithdraw(BaseModel):
    """A requester withdrawing their own request (recoverable cancel)."""
    reason: str = Field(min_length=2, max_length=500)


class SelfTicketReopen(BaseModel):
    """A requester reopening their own RESOLVED ticket — free-text reason required,
    coded verdict (ReopenReason) optional."""
    reason: str = Field(min_length=2, max_length=500)
    reason_code: Optional[str] = None


class SelfTicketAssign(BaseModel):
    """Manager / team-member assigning one of their team's tickets to another member."""
    assigned_agent_id: UUID


class CollaboratorChange(BaseModel):
    """Add / remove a collaborator (an extra person who can see + work the ticket)."""
    user_id: UUID


class AssigneeOption(BaseModel):
    """One candidate the caller may route a given ticket to (member of its team / a report)."""
    id: UUID
    name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None          # lead | agent | collaborator | report | me
    is_agent: bool = False
    is_current: bool = False            # currently the assignee?


class MyCapabilities(BaseModel):
    """What the current user may do on the support desk — drives the role-adaptive UI."""
    is_admin: bool = False
    is_agent: bool = False
    is_manager: bool = False
    team_size: int = 0
    # Support-team membership: teams I'm on / teams I LEAD (drawer owner-tier gating).
    member_team_ids: List[UUID] = Field(default_factory=list)
    lead_team_ids: List[UUID] = Field(default_factory=list)


class TeamMember(BaseModel):
    id: UUID
    name: Optional[str] = None
    email: Optional[str] = None


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_number: str
    subject: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    subcategory_id: Optional[UUID] = None
    ticket_type: str
    priority: str
    source: str
    status: str

    organization_id: Optional[UUID] = None
    customer_id: Optional[UUID] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None
    department: Optional[str] = None
    location: Optional[str] = None

    is_internal: bool
    raised_by_user_id: Optional[UUID] = None

    support_team: Optional[str] = None
    team_id: Optional[UUID] = None
    queue_id: Optional[UUID] = None
    assigned_agent_id: Optional[UUID] = None
    assigned_engineer_id: Optional[UUID] = None
    assigned_pm_id: Optional[UUID] = None
    collaborators: List[Any] = Field(default_factory=list)

    sla_package_id: Optional[UUID] = None
    response_due_at: Optional[datetime] = None
    resolution_due_at: Optional[datetime] = None
    first_responded_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    sla_response_breached: bool
    sla_resolution_breached: bool
    # Breach-detection stamps (Breached desk): the DUE instant each target was missed at
    # (honest aging — cleared if a pause-resume pushes the deadline back out).
    sla_response_breached_at: Optional[datetime] = None
    sla_resolution_breached_at: Optional[datetime] = None
    sla_paused_since: Optional[datetime] = None   # clock frozen since (null = running)
    sla_paused_ms: int = 0                         # total SLA time paused (stop-the-clock)

    is_escalated: bool
    escalation_level: int
    escalated_at: Optional[datetime] = None
    escalation_reason: Optional[str] = None
    # Structured escalation record ("Thermal Updraft" desk — all additive; old rows None)
    escalation_type: Optional[str] = None
    escalation_reason_code: Optional[str] = None
    escalated_by_id: Optional[UUID] = None
    escalated_to_team_id: Optional[UUID] = None
    escalation_acknowledged_at: Optional[datetime] = None
    escalation_acknowledged_by_id: Optional[UUID] = None
    escalation_response_due_at: Optional[datetime] = None
    auto_escalated_at: Optional[datetime] = None
    reopened_count: int
    reopen_reason: Optional[str] = None
    # Reopen lifecycle ("Möbius Loop" desk — all additive; old rows None)
    reopen_reason_code: Optional[str] = None
    reopen_source: Optional[str] = None            # requester|agent|portal|auto
    last_reopened_at: Optional[datetime] = None
    last_reopened_by_id: Optional[UUID] = None
    reopen_latency_ms: Optional[int] = None        # resolved→reopen gap of the LAST cycle
    prev_resolution_code: Optional[str] = None     # the failed fix, preserved for context
    prev_resolution_summary: Optional[str] = None
    prev_resolved_at: Optional[datetime] = None

    # Phase 2 — hold / reminders / vendor / incident / RCA
    hold_reason: Optional[str] = None
    hold_until: Optional[datetime] = None
    held_at: Optional[datetime] = None
    # Suspension Dock — hold governance + telemetry (all additive; only meaningful while
    # status == on_hold). held_from_status = where Resume returns the ticket;
    # time_on_hold_ms / auto_resume_at / hold_stale are computed by enrich_tickets.
    held_from_status: Optional[str] = None
    hold_reason_code: Optional[str] = None
    last_hold_review_at: Optional[datetime] = None
    hold_review_count: int = 0
    time_on_hold_ms: Optional[int] = None
    auto_resume_at: Optional[datetime] = None
    hold_stale: Optional[bool] = None
    last_customer_reply_at: Optional[datetime] = None
    reminder_count: int = 0
    last_reminder_at: Optional[datetime] = None
    vendor_name: Optional[str] = None
    vendor_ticket_ref: Optional[str] = None
    vendor_status: Optional[str] = None
    # Pending-vendor lifecycle ("Vendor Relay Station")
    vendor_dispatched_at: Optional[datetime] = None
    vendor_due_at: Optional[datetime] = None
    vendor_reply_at: Optional[datetime] = None
    vendor_reminder_count: int = 0
    last_vendor_reminder_at: Optional[datetime] = None
    vendor_wait_reason: Optional[str] = None
    vendor_po_ref: Optional[str] = None
    is_major_incident: bool = False
    business_impact: Optional[str] = None
    affected_users: Optional[int] = None
    revenue_impact: Optional[str] = None
    war_room_url: Optional[str] = None
    # War Room — ACK + stakeholder-update cadence (all additive; old rows stay None)
    acknowledged_at: Optional[datetime] = None
    acknowledged_by_id: Optional[UUID] = None
    update_interval_minutes: Optional[int] = None
    next_update_due_at: Optional[datetime] = None
    last_status_update_at: Optional[datetime] = None
    # Incident command (Fault Grid) — roster + impact detail + parent rollup, so the
    # drawer's incident modals open pre-filled (all additive; non-incidents stay None)
    incident_commander_id: Optional[UUID] = None
    comms_lead_id: Optional[UUID] = None
    ops_lead_id: Optional[UUID] = None
    affected_services: List[str] = Field(default_factory=list)
    incident_started_at: Optional[datetime] = None
    incident_detected_at: Optional[datetime] = None
    compliance_impact: bool = False
    security_impact: bool = False
    public_impact: bool = False
    parent_incident_id: Optional[UUID] = None
    breach_reason: Optional[str] = None
    rca_summary: Optional[str] = None
    rca_corrective: Optional[str] = None
    rca_preventive: Optional[str] = None
    # RCA v2 — structured capture + review workflow (all additive)
    rca_status: Optional[str] = None               # filed|validated|returned|stale (raw column)
    rca_category: Optional[str] = None             # RootCauseCategory value
    rca_five_whys: Optional[List[str]] = None
    rca_factors: Optional[List[str]] = None
    rca_filed_at: Optional[datetime] = None
    rca_filed_by_id: Optional[UUID] = None
    rca_reviewed_at: Optional[datetime] = None
    rca_reviewed_by_id: Optional[UUID] = None
    rca_review_note: Optional[str] = None
    rca_inherited_from_problem_id: Optional[UUID] = None

    # Agent workbench (ITIL triage + resolve + merge + time)
    sub_status: Optional[str] = None
    impact: Optional[str] = None
    urgency: Optional[str] = None
    resolution_code: Optional[str] = None
    resolution_summary: Optional[str] = None
    resolution_category: Optional[str] = None
    resolved_by_id: Optional[UUID] = None          # who recorded the fix (NULL = system)
    closed_by_id: Optional[UUID] = None
    time_spent_minutes: int = 0
    merged_into_id: Optional[UUID] = None
    follow_up_of_id: Optional[UUID] = None         # this case continues a sealed terminal ticket
    last_viewed_at: Optional[datetime] = None

    linked_change_id: Optional[UUID] = None
    linked_problem_id: Optional[UUID] = None
    links: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)

    csat_score: Optional[int] = None
    csat_comment: Optional[str] = None

    # Deep Storage (Archived desk): the tombstone flag + archive provenance. is_deleted
    # was never serialized before — archived rows only ever surfaced via scope=archived,
    # but the drawer needs it to render the deep-storage banner on a raw detail fetch.
    is_deleted: bool = False
    archived_at: Optional[datetime] = None
    archived_by_id: Optional[UUID] = None
    archive_reason_code: Optional[str] = None
    legal_hold: bool = False

    created_at: datetime
    updated_at: datetime

    # enriched (router-attached)
    category_name: Optional[str] = None
    subcategory_name: Optional[str] = None
    organization_name: Optional[str] = None
    customer_name: Optional[str] = None
    assigned_agent_name: Optional[str] = None
    raised_by_name: Optional[str] = None
    team_name: Optional[str] = None
    collaborator_people: List[Dict[str, Any]] = Field(default_factory=list)  # [{id, name}]
    sla_response_state: Optional[str] = None     # ok | due-soon | breached | met
    sla_resolution_state: Optional[str] = None
    comment_count: Optional[int] = None
    # Pending-customer telemetry (router-attached; only populated while awaiting the customer).
    pending_since: Optional[datetime] = None      # when the SLA clock was paused (pause states)
    silence_ms: Optional[int] = None              # ms since the last customer contact / pending entry
    auto_close_at: Optional[datetime] = None       # when the pending-customer sweep will auto-resolve it
    # Pending-vendor telemetry (router-attached; only meaningful while waiting on a vendor).
    vendor_wait_ms: Optional[int] = None          # ms since the hand-off (vendor_dispatched_at / pause entry)
    vendor_overdue: Optional[bool] = None         # now past vendor_due_at (vendor OLA breached)
    vendor_coordinator_name: Optional[str] = None  # internal owner of the vendor relationship (assigned engineer)
    # War-room telemetry (router-attached)
    acknowledged_by_name: Optional[str] = None
    update_due_ms: Optional[int] = None           # ms until the next promised update (negative = overdue)
    update_overdue: Optional[bool] = None         # cadence armed AND past next_update_due_at
    # Escalation telemetry (router-attached; only populated while is_escalated)
    escalated_by_name: Optional[str] = None
    escalated_to_team_name: Optional[str] = None
    escalation_acknowledged_by_name: Optional[str] = None
    escalation_acked: Optional[bool] = None       # None when not escalated
    esc_response_due_ms: Optional[int] = None     # ms until the ack deadline (negative = overdue; None once acked/terminal)
    esc_response_overdue: Optional[bool] = None   # unacked AND past escalation_response_due_at
    time_since_escalated_ms: Optional[int] = None  # dwell since the last tier lift
    auto_escalated: Optional[bool] = None          # raised by the SLA-breach sweep
    # Reopen telemetry (router-attached)
    last_reopened_by_name: Optional[str] = None
    # Resolved-desk telemetry (router-attached): resolver display name + the public-staff
    # touch count powering the FCR ("one-touch") lens. auto_close_at above doubles as the
    # resolved→closed countdown while status == resolved.
    resolved_by_name: Optional[str] = None
    agent_public_comment_count: Optional[int] = None
    # Closed-desk telemetry (router-attached): who sealed the record (None = the auto-close
    # sweep / System) + the human number of the ticket this one follows up on.
    closed_by_name: Optional[str] = None
    follow_up_of_number: Optional[str] = None
    # Deep-storage telemetry (router-attached; only meaningful while is_deleted): who
    # shelved the record + the retention countdown. purge_eligible_at is None (suspended)
    # under legal hold; purge_eligible is False until the window lapses.
    archived_by_name: Optional[str] = None
    purge_eligible_at: Optional[datetime] = None
    purge_eligible: Optional[bool] = None
    # Team Ops telemetry (router-attached; team-queue list only): who ELSE has this
    # ticket open right now — Zendesk-style collision pips. [{user_id, name}]
    viewers: List[Dict[str, Any]] = Field(default_factory=list)

    # JSON columns are nullable in the DB (a row created before a default, or via a path that
    # never set them, stores NULL). Pydantic's default_factory only fills a MISSING field — an
    # explicit None still fails validation and, in a list endpoint, one bad row 500s the WHOLE
    # response (every ticket vanishes). Coerce NULL → empty so a stray null never blanks the desk.
    @field_validator("links", mode="before")
    @classmethod
    def _links_not_none(cls, v):
        return v if isinstance(v, dict) else {}

    @field_validator("attachments", "tags", "collaborators", "collaborator_people", "viewers", mode="before")
    @classmethod
    def _list_not_none(cls, v):
        return v if isinstance(v, list) else []


class TicketDetailResponse(TicketResponse):
    comments: List[CommentResponse] = Field(default_factory=list)
    activities: List[ActivityResponse] = Field(default_factory=list)
    viewer_can_work: bool = False   # may the current viewer work it (assign/resolve/collaborate)?


class TicketListResponse(BaseModel):
    items: List[TicketResponse]
    total: int
    page: int
    limit: int


# ─────────────────────── Agent workbench (KPIs + AI insights) ───────────────────────
class WorkbenchInsight(BaseModel):
    """A single smart-insight surfaced on the workbench. Heuristic today; the
    compute seam can be swapped for an LLM later without changing this contract."""
    id: str
    kind: str               # breach_risk|merge|customer_flood|category_spike|workload|stale|pending_nudge
    severity: str           # info|warn|crit
    title: str
    detail: Optional[str] = None
    action: Optional[str] = None        # view|assign|merge|escalate|resolve|reply
    ticket_ids: List[UUID] = Field(default_factory=list)


class WorkbenchStats(BaseModel):
    open: int = 0
    in_progress: int = 0
    pending_customer: int = 0
    pending_vendor: int = 0
    vendor_overdue: int = 0             # pending-vendor tickets past their vendor OLA/ETA
    vendor_dispatched_today: int = 0    # hand-offs to a vendor stamped today
    pending_total: int = 0
    on_hold: int = 0
    sla_risk: int = 0           # approaching SLA (due soon, not yet breached)
    sla_breached: int = 0
    critical: int = 0
    escalated: int = 0
    resolved_today: int = 0
    avg_resolution_minutes: Optional[float] = None
    total_active: int = 0
    workload_score: int = 0     # 0..100 (heuristic)
    insights: List[WorkbenchInsight] = Field(default_factory=list)


# ──────────────────── Team command center (All Tickets) ────────────────────
class SquadLoad(BaseModel):
    """One team member's live load — powers the per-agent squad bars + swimlane headers."""
    agent_id: UUID
    name: Optional[str] = None
    open: int = 0
    breaching: int = 0
    critical: int = 0


class FastestLap(BaseModel):
    """Top resolver today (the F1 'fastest lap' flag)."""
    agent_id: Optional[UUID] = None
    name: Optional[str] = None
    count: int = 0


class CommandCenterStats(WorkbenchStats):
    """Team-scoped command-center aggregate: WorkbenchStats (open/pending/sla/insights/…)
    PLUS team-level situational tallies for the F1 flag board + squad-load row."""
    total: int = 0
    unassigned_in_scope: int = 0
    triage_pool: int = 0
    due_soon: int = 0
    breaching: int = 0          # alias of sla_breached, exposed for the flag board
    # F1 flag board (live SLA health of the team queue)
    flag_green: int = 0         # active + on-track
    flag_amber: int = 0         # due soon
    flag_red: int = 0           # breached
    flag_safety_car: int = 0    # on hold / paused
    fastest_lap: Optional[FastestLap] = None
    squad: List[SquadLoad] = Field(default_factory=list)
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


# ──────────────────── Critical war room (Critical tickets) ────────────────────
class CriticalStats(BaseModel):
    """Team-scoped critical-ops aggregate — same seal as the command-center list
    (superuser = whole desk), computed over priority=critical ∪ major incidents so the
    lenses on the Critical board reconcile with the list."""
    active_critical: int = 0        # non-terminal criticals/MIs in scope
    major_incidents: int = 0        # is_major_incident, non-terminal
    breaching: int = 0              # response/resolution breached among active
    due_soon: int = 0               # resolution due within 2h (unpaused, active)
    unacked: int = 0                # active AND acknowledged_at IS NULL
    update_overdue: int = 0         # cadence armed AND next_update_due_at < now (active)
    no_owner: int = 0               # active AND assigned_agent_id IS NULL
    oldest_age_minutes: int = 0     # oldest active critical
    mtta_minutes: Optional[float] = None   # mean created→acknowledged, acked in last 30d
    mttr_minutes: Optional[float] = None   # mean created→resolved, resolved in last 30d
    resolved_today: int = 0
    ack_coverage: int = 0           # 0..100 = acked / active
    missing_rca: int = 0            # terminal in last 30d with no rca_summary
    by_business_impact: Dict[str, int] = Field(default_factory=dict)
    squad: List[SquadLoad] = Field(default_factory=list)
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


# ──────────────────── Escalated desk ("Thermal Updraft") ────────────────────
class EscalationEvent(BaseModel):
    """One rung of a ticket's escalation history — derived from the activity timeline
    (actions escalated/de_escalated), enriched with dwell (time spent at that level)."""
    at: datetime
    action: str                       # escalated | de_escalated
    level: int = 0
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    reason: Optional[str] = None
    reason_code: Optional[str] = None
    escalation_type: Optional[str] = None
    to_team_id: Optional[UUID] = None
    to_team_name: Optional[str] = None
    auto: bool = False
    dwell_ms: Optional[int] = None    # time until the next event (last: until now, while escalated)


class EscalationStats(BaseModel):
    """Team-sealed escalated-desk aggregate — same seal as the command-center list
    (superuser = whole desk), computed over is_escalated tickets so the Thermal-Updraft
    lenses reconcile with the working set."""
    active_escalations: int = 0          # non-terminal escalated tickets in scope
    by_level: Dict[str, int] = Field(default_factory=dict)       # {"1": n, "2": n, "3+": n}
    by_type: Dict[str, int] = Field(default_factory=dict)        # hierarchical|functional|unset
    by_reason_code: Dict[str, int] = Field(default_factory=dict)
    unacked: int = 0                     # active AND escalation_acknowledged_at IS NULL
    esc_response_overdue: int = 0        # unacked AND past the ack deadline
    breaching_sla: int = 0               # response/resolution breached among active
    no_owner: int = 0
    auto_escalated_count: int = 0        # active, raised by the SLA-breach sweep
    sla_breach_candidates: int = 0       # breached, NOT escalated, non-terminal (no-write lens)
    oldest_escalation_age_minutes: int = 0
    avg_dwell_minutes: Optional[float] = None      # mean now−escalated_at over active
    emtta_minutes: Optional[float] = None          # mean escalated→esc-acked, acked last 30d
    de_escalated_today: int = 0
    resolved_today: int = 0                        # escalated & resolved today, excl. merged
    ack_coverage: int = 0                          # 0..100 = esc-acked / active
    squad: List[SquadLoad] = Field(default_factory=list)  # open=active esc · breaching · critical=L2+
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


class BreachedStats(BaseModel):
    """Team-sealed Breached-desk aggregate (the "Time-Debt Meter") — same seal as the
    command-center list (superuser = whole desk), computed over stored-breach-flag tickets
    AFTER the breach sweep runs, so the lenses reconcile with the working set."""
    active_breached: int = 0             # non-terminal breached tickets in scope
    swept_now: int = 0                   # flags freshly flipped by the sweep on this call
    by_kind: Dict[str, int] = Field(default_factory=dict)      # {"response","resolution","both"} (active)
    by_priority: Dict[str, int] = Field(default_factory=dict)
    by_status: Dict[str, int] = Field(default_factory=dict)
    by_age: Dict[str, int] = Field(default_factory=dict)       # {"<2h","2-8h","8-24h",">24h"} from breached_at
    unassigned_breached: int = 0
    not_escalated: int = 0               # active AND is_escalated = False (auto-escalation gap)
    oldest_breach_age_minutes: int = 0
    total_debt_minutes: int = 0          # Σ overage across active resolution-breached (pause-aware)
    avg_overage_minutes: Optional[float] = None   # pause-aware, active resolution-breached
    max_overage_minutes: Optional[float] = None
    at_risk: int = 0                     # unpaused, un-breached, resolution due ≤ 2h
    imminent: int = 0                    # …due ≤ 30m
    repaired_today: int = 0              # breached AND resolved today (excl. merged)
    avg_repair_overrun_minutes: Optional[float] = None  # resolved_at − resolution_due_at, late-resolved 30d
    missing_rca: int = 0                 # breached, no breach_reason AND no rca_summary
    rca_coverage: int = 0                # 0..100 over the whole breached base
    squad: List[SquadLoad] = Field(default_factory=list)  # open=active breached · breaching=both-kind · critical
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


class OverdueWorst(BaseModel):
    """The single deepest ticket in the Gravity Well — the desk's worst overdue."""
    ticket_id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    priority: Optional[str] = None
    late_minutes: int = 0


class OverdueStats(BaseModel):
    """Team-sealed Overdue-desk aggregate (the "Gravity Well" recovery desk) — same seal
    as the command-center list (superuser = whole desk). Overdue = OPEN statuses with the
    clock RUNNING (sla_paused_since IS NULL) past a due date — resolution and/or first
    response — so unlike the Breached ledger every number here is still recoverable.
    Runs the breach-flag sweep first so the lenses reconcile with the list."""
    total: int = 0                        # any-kind overdue (response ∪ resolution)
    swept_now: int = 0                    # breach flags freshly flipped by the sweep on this call
    resolution_overdue: int = 0           # past resolution_due_at (may overlap response_overdue)
    response_overdue: int = 0             # past response_due_at with NO first response yet
    both_overdue: int = 0                 # past both clocks
    unassigned: int = 0
    not_escalated: int = 0                # overdue AND is_escalated = False
    critical: int = 0
    frozen_excluded: int = 0              # paused past-due (pending/hold) — context, NOT overdue
    by_priority: Dict[str, int] = Field(default_factory=dict)
    by_status: Dict[str, int] = Field(default_factory=dict)
    by_late: Dict[str, int] = Field(default_factory=dict)   # {"<1h","1-4h","4-24h","1-3d",">3d"}
    total_late_minutes: int = 0           # Σ now − resolution_due_at over resolution-overdue
    avg_late_minutes: Optional[float] = None
    max_late_minutes: Optional[float] = None
    oldest: Optional[OverdueWorst] = None
    at_risk: int = 0                      # tipping point: clock running, due ≤ 2h (scope=due_soon)
    imminent: int = 0                     # …due ≤ 30m
    recovered_today: int = 0              # resolved today AFTER its resolution due (excl. merged)
    recovered_today_avg_late_minutes: Optional[float] = None
    squad: List[SquadLoad] = Field(default_factory=list)  # open=overdue · breaching=both-kind · critical
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


class ReopenedWorst(BaseModel):
    """The stuck rider — the ticket with the most reopen cycles on the Möbius loop."""
    ticket_id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    priority: Optional[str] = None
    reopened_count: int = 0
    last_reopened_at: Optional[datetime] = None


class ReopenedStats(BaseModel):
    """Team-sealed Reopened-desk aggregate (the "Möbius Loop" desk) — same seal as the
    command-center list (superuser = whole desk). 'Reopened' is a lifetime marker
    (reopened_count > 0), NOT a status: ACTIVE rows are back on the desk right now,
    RE-RESOLVED rows made it off the loop again. reopen_rate_30d = reopens ÷ (surviving
    resolves + reopens) in the window — the honest Zendesk-style reopens-per-solve ratio
    (a reopen clears the prior resolved_at stamp, so raw resolves alone would undercount)."""
    total_reopened: int = 0
    active_reopened: int = 0              # back on the desk now (non-terminal)
    re_resolved: int = 0                  # resolved/closed again after >=1 reopen
    re_resolved_today: int = 0
    chronic: int = 0                      # reopened_count >= CHRONIC_REOPEN_THRESHOLD (lifetime)
    chronic_open: int = 0                 # …and still active
    unassigned_reopened: int = 0          # active but nobody owns the re-fix
    critical_reopened: int = 0
    re_breached: int = 0                  # active AND missing the FRESH re-resolution deadline
    due_soon_reopened: int = 0            # active, clock running, fresh due <= 2h out
    max_reopens: int = 0
    worst: Optional[ReopenedWorst] = None
    by_source: Dict[str, int] = Field(default_factory=dict)   # requester|agent|portal|auto|unrecorded
    by_reason: Dict[str, int] = Field(default_factory=dict)   # ReopenReason codes|uncoded
    by_priority: Dict[str, int] = Field(default_factory=dict)
    by_status: Dict[str, int] = Field(default_factory=dict)
    reopens_30d: int = 0
    resolved_30d: int = 0
    reopen_rate_30d: float = 0.0          # percent
    avg_time_to_reopen_minutes: Optional[float] = None   # resolved→reopen gap (30d window)
    max_time_to_reopen_minutes: Optional[float] = None
    avg_cycle_age_minutes: Optional[float] = None        # how long active riders have been back
    squad: List[SquadLoad] = Field(default_factory=list)  # open=active reopened · breaching=re-breached
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


# ──────────────────── Resolved desk (the closeout / quality gate) ────────────────────
class ResolutionTrendBucket(BaseModel):
    """One day of the resolution trend chart: how many fixes landed vs bounced back."""
    day: datetime
    resolved: int = 0
    reopened: int = 0


class ResolverLoad(BaseModel):
    """One resolver's 30-day closeout record — powers the leaderboard. Attribution =
    coalesce(resolved_by_id, assigned_agent_id) so legacy rows still count."""
    agent_id: UUID
    name: Optional[str] = None
    resolved_30d: int = 0
    csat_avg: Optional[float] = None
    avg_ttr_minutes: Optional[float] = None
    low_csat: int = 0


class ResolvedStats(BaseModel):
    """Team-sealed Resolved-desk aggregate — same seal as the command-center list
    (superuser = whole desk), merged tombstones excluded. The desk has two populations:
    the SHELF (status=resolved, inside the 3-day auto-close/reopen window) and the 30-day
    resolution record (resolved_at window — reopens clear resolved_at, so this is the
    SURVIVING record; bounced fixes live in reopens_30d)."""
    # shelf (pending close)
    resolved_now: int = 0                 # status == resolved (the pre-close shelf)
    pending_close: int = 0                # alias of resolved_now (lens naming)
    due_close_24h: int = 0                # auto-closes inside the next 24h
    overdue_close: int = 0                # past the window, awaiting the sweep
    soonest_auto_close_at: Optional[datetime] = None
    unrated_shelf: int = 0                # shelf tickets with no CSAT yet (rating window open)
    closed_total: int = 0                 # status == closed (seal-scoped, lifetime)
    # throughput
    resolved_today: int = 0
    resolved_7d: int = 0
    resolved_30d: int = 0
    trend: List[ResolutionTrendBucket] = Field(default_factory=list)   # 14 daily buckets
    # speed (30d, pause-credited)
    mttr_avg_minutes: Optional[float] = None
    mttr_p50_minutes: Optional[float] = None
    mttr_p90_minutes: Optional[float] = None
    mttr_by_priority: Dict[str, float] = Field(default_factory=dict)
    avg_time_spent_minutes: Optional[float] = None
    # quality
    fcr_30d: int = 0                      # one-touch: never reopened, <=1 public staff reply
    fcr_30d_pct: Optional[float] = None
    reopens_30d: int = 0                  # bounced fixes (same window)
    survived_30d: int = 0                 # = resolved_30d (still standing)
    reopen_rate_30d: float = 0.0          # bounced / (survived + bounced), percent
    sla_met_30d: int = 0                  # resolved with the resolution SLA intact
    sla_met_pct_30d: Optional[float] = None
    # CSAT (30d resolution record)
    csat_avg: Optional[float] = None
    csat_count: int = 0
    csat_coverage_pct: Optional[float] = None
    csat_low: int = 0                     # score <= 2
    csat_dist: Dict[str, int] = Field(default_factory=dict)   # {"1".."5": n}
    # composition (30d)
    by_resolution_code: Dict[str, int] = Field(default_factory=dict)
    by_root_cause: Dict[str, int] = Field(default_factory=dict)
    by_priority: Dict[str, int] = Field(default_factory=dict)
    # people
    leaderboard: List[ResolverLoad] = Field(default_factory=list)      # top 8 by resolves
    squad: List[SquadLoad] = Field(default_factory=list)
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


# ──────────────────── Closed desk (the archive of record) ────────────────────
class ClosureTrendBucket(BaseModel):
    """One month of the closure chronicle: how many records were sealed."""
    month: datetime
    closed: int = 0


class CloserLoad(BaseModel):
    """One closer's 30-day sealing record — powers the top-closers board. Attribution =
    coalesce(closed_by_id, resolved_by_id, assigned_agent_id) so merge/legacy rows count."""
    agent_id: UUID
    name: Optional[str] = None
    closed_30d: int = 0
    csat_avg: Optional[float] = None
    avg_lifespan_minutes: Optional[float] = None


class ClosedStats(BaseModel):
    """Team-sealed Closed-desk aggregate — same seal as the command-center list
    (superuser = whole desk). Unlike ResolvedStats, merged tombstones ARE part of the
    archive population (they're real records of closure); they're excluded only from
    the quality math (CSAT / lifespan / leaderboard) so duplicates don't distort it."""
    # volume
    closed_today: int = 0
    closed_7d: int = 0
    closed_30d: int = 0
    closed_total: int = 0                 # lifetime, seal-scoped
    resolved_waiting: int = 0             # status == resolved (the shelf feeding this desk)
    # closure-source mix (30d cohort by closed_at)
    by_close_source: Dict[str, int] = Field(default_factory=dict)  # auto_sweep|manual|merged|withdrawn|no_response
    merged_total: int = 0                 # lifetime merged tombstones
    # composition (30d)
    by_resolution_code: Dict[str, int] = Field(default_factory=dict)
    by_root_cause: Dict[str, int] = Field(default_factory=dict)
    by_priority: Dict[str, int] = Field(default_factory=dict)
    uncoded_30d: int = 0                  # closed without a resolution code (merges/legacy)
    # full lifespan created→closed, pause-credited (30d, real records only)
    lifespan_avg_minutes: Optional[float] = None
    lifespan_p50_minutes: Optional[float] = None
    lifespan_p90_minutes: Optional[float] = None
    lifespan_by_priority: Dict[str, float] = Field(default_factory=dict)
    # permanence (does the seal hold?)
    reopened_from_closed_30d: int = 0     # exhumed records (agent reopen from CLOSED)
    closure_survival_pct_30d: Optional[float] = None
    # CSAT of record (30d closed cohort — csat survives close)
    csat_avg: Optional[float] = None
    csat_count: int = 0
    csat_coverage_pct: Optional[float] = None
    csat_low: int = 0                     # score <= 2
    csat_dist: Dict[str, int] = Field(default_factory=dict)   # {"1".."5": n}
    # knowledge & follow-through
    kb_candidates_30d: int = 0            # promotable fixes not yet in the KB
    kb_promoted_total: int = 0            # records that seeded an article (links ? kb_article_id)
    follow_ups_30d: int = 0               # follow-up cases spawned in the window
    open_follow_ups: int = 0              # of those, still being worked
    # chronicle + people
    trend: List[ClosureTrendBucket] = Field(default_factory=list)   # 12 monthly cohorts
    leaderboard: List[CloserLoad] = Field(default_factory=list)     # top 8 closers
    auto_closed_30d: int = 0              # sealed by the sweep (System row client-side)
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


# ─────────────────────── Archived "Deep Storage" desk stats ───────────────────────
class ArchiveTrendBucket(BaseModel):
    """One month of the storage chronicle: records shelved vs pulled back."""
    month: datetime
    archived: int = 0
    restored: int = 0


class ArchiverLoad(BaseModel):
    """One archiver's storage record — powers the top-archivers strip."""
    agent_id: UUID
    name: Optional[str] = None
    archived_total: int = 0
    archived_30d: int = 0


class ArchivedStats(BaseModel):
    """Team-sealed Archived-desk aggregate — same seal as the command-center list
    (superuser = whole desk). Population = tombstones (is_deleted=True) only; the
    retention sweep runs first so the shelf is honest the moment it renders."""
    # volume
    archived_today: int = 0
    archived_7d: int = 0
    archived_30d: int = 0
    total_archived: int = 0               # everything currently in deep storage (seal-scoped)
    restored_30d: int = 0                 # pulled back into circulation (activity-derived)
    # composition
    by_reason_code: Dict[str, int] = Field(default_factory=dict)   # incl. 'uncoded' legacy rows
    by_status_at_archive: Dict[str, int] = Field(default_factory=dict)  # status is preserved on the row
    by_priority: Dict[str, int] = Field(default_factory=dict)
    open_at_archive: int = 0              # shelved while still open/in_progress (mistake candidates)
    uncoded: int = 0                      # archived before the taxonomy existed
    # age strata (dormancy = now - archived_at)
    age_cohorts: Dict[str, int] = Field(default_factory=dict)      # lt_7d | d7_30 | d30_90 | gt_90
    dormancy_p50_minutes: Optional[float] = None
    oldest_archived_at: Optional[datetime] = None
    # retention & governance
    purge_eligible_count: int = 0         # past the retention window, not held
    expiring_soon_count: int = 0          # within SUPPORT_ARCHIVE_EXPIRING_SOON_DAYS of eligibility
    legal_hold_count: int = 0
    auto_archived_30d: int = 0            # shelved by the retention sweep
    retention_days: int = 0               # SUPPORT_ARCHIVE_RETENTION_DAYS (client renders real policy)
    autoarchive_days: Optional[int] = None  # SUPPORT_CLOSED_AUTOARCHIVE_DAYS (None = sweep off)
    # chronicle + people
    trend: List[ArchiveTrendBucket] = Field(default_factory=list)  # 12 monthly cohorts
    top_archivers: List[ArchiverLoad] = Field(default_factory=list)  # top 8 (manual archives)
    restored_by_30d: Dict[str, int] = Field(default_factory=dict)  # name → restores
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


class TicketFollowUpCreate(BaseModel):
    """Spawn a linked follow-up case from a terminal ticket (the Zendesk pattern:
    closed records are immutable — continue the story in a fresh, linked ticket)."""
    subject: Optional[str] = None
    description: str = Field(min_length=3)
    priority: Optional[str] = None
    ticket_type: Optional[str] = None
    category_id: Optional[UUID] = None
    tags: Optional[List[str]] = None
    assign_me: bool = False


class TicketKbPromote(BaseModel):
    """KCS: promote a sealed ticket's resolution into a DRAFT knowledge article.
    Status is server-forced to draft; publishing stays an editorial (superuser) act."""
    title: Optional[str] = None
    body: Optional[str] = None
    kb_category_id: Optional[UUID] = None
    visibility: Optional[str] = None      # default 'internal'


class MergeChainNode(BaseModel):
    """One ticket in a merge lineage (masters above, folded duplicates below)."""
    id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    status: Optional[str] = None
    closed_at: Optional[datetime] = None
    merged_into_id: Optional[UUID] = None


class TicketMergeChain(BaseModel):
    masters: List[MergeChainNode] = Field(default_factory=list)     # walked UP merged_into_id
    duplicates: List[MergeChainNode] = Field(default_factory=list)  # folded INTO this ticket


class TicketNudgeOwner(BaseModel):
    """Manual 'nudge the owner' on a late ticket — optional message rides the timeline
    entry + the notification. Day-throttled per ticket server-side."""
    message: Optional[str] = None


class TicketViewerInfo(BaseModel):
    """One live viewer of a ticket (agent-collision presence)."""
    user_id: UUID
    name: Optional[str] = None
    last_seen_at: Optional[datetime] = None
    is_me: bool = False


class TicketPresenceResponse(BaseModel):
    """Everyone with this ticket open right now (heartbeats seen in the last minute)."""
    ticket_id: UUID
    viewers: List[TicketViewerInfo] = Field(default_factory=list)


# ──────────────────── Unassigned intake queue (Claim Field) ────────────────────
class UnassignedQueueTeam(BaseModel):
    """One team's slice of the already-routed unassigned pool — drives the lane picker."""
    team_id: Optional[UUID] = None
    name: Optional[str] = None
    count: int = 0


class UnassignedQueueStats(BaseModel):
    """Aggregate for the Unassigned queue — the caller's 'claimable pool' (their teams +
    the triage pool routing to their teams). Same scope as the list, so counts reconcile."""
    total: int = 0
    team_queue: int = 0          # unowned tickets already routed to one of my teams
    triage_pool: int = 0         # untriaged (team_id NULL) tickets routing to my teams
    breaching: int = 0
    due_soon: int = 0
    oldest_age_minutes: int = 0
    by_priority: Dict[str, int] = Field(default_factory=dict)
    teams: List[UnassignedQueueTeam] = Field(default_factory=list)


class ClaimNext(BaseModel):
    """Guided-mode 'Claim Next' — optionally narrow the pick to a lane / a specific team."""
    lane: Optional[str] = None       # all | team | triage
    team_id: Optional[UUID] = None


class ClaimTicket(BaseModel):
    """Deliberate single-ticket claim from the Unassigned queue (an optional internal note
    captured on the timeline). Eligibility (caller on a team that handles the ticket) is
    enforced server-side."""
    note: Optional[str] = None


# ──────────────────── Team Ops desk (agent-side Team Tickets) ────────────────────
class TeamSwitcherEntry(BaseModel):
    """One team the caller belongs to — drives the multi-team switcher chips."""
    id: UUID
    name: Optional[str] = None
    color: Optional[str] = None
    is_lead: bool = False
    member_count: int = 0
    open_count: int = 0


class TeamRosterEntry(BaseModel):
    """One roster member's live telemetry — powers the roster deck (load ring, breach
    pips, aging micro-bars, shift dot). Collaborators are listed for visibility but
    carry role='collaborator' so the UI can exclude them from load math."""
    agent_id: UUID
    name: Optional[str] = None
    role: str = "agent"                 # lead | agent | collaborator
    is_lead: bool = False
    on_shift: Optional[bool] = None     # from the team's business_hours; None = unknown
    open: int = 0                       # non-terminal load (incl. on hold)
    in_progress: int = 0
    pending: int = 0                    # pending_customer + pending_vendor + on_hold
    breaching: int = 0
    critical: int = 0
    due_soon: int = 0
    idle: int = 0                       # active, no update for TEAM_IDLE_HOURS
    aging_1d: int = 0                   # active tickets younger than 1 day
    aging_3d: int = 0                   # 1–3 days
    aging_7d: int = 0                   # 3–7 days
    aging_7plus: int = 0                # older than 7 days
    resolved_7d: int = 0
    csat_avg: Optional[float] = None


class TeamFlowBucket(BaseModel):
    """One day of the 14-day inflow (created) vs outflow (resolved) balance."""
    day: datetime
    inflow: int = 0
    outflow: int = 0


class TeamHotspot(BaseModel):
    """A collision hotspot — a team ticket ≥2 agents have open right now."""
    ticket_id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    viewer_count: int = 0
    viewer_names: List[str] = Field(default_factory=list)


class TeamLeaderEntry(BaseModel):
    """Leaderboard row — who shipped the most fixes this week (CSAT alongside)."""
    agent_id: UUID
    name: Optional[str] = None
    resolved_7d: int = 0
    csat_avg: Optional[float] = None


class TeamQueueStats(BaseModel):
    """Aggregate for the agent-side Team Ops desk — same seal as the team-queue list
    (tickets routed to the caller's team(s); superuser = any team), so every lens count
    reconciles with the working set."""
    # switcher + selected-team identity
    teams: List[TeamSwitcherEntry] = Field(default_factory=list)
    team_id: Optional[UUID] = None          # the selected team (None = all my teams)
    team_name: Optional[str] = None
    team_color: Optional[str] = None
    lead_name: Optional[str] = None
    assignment_method: Optional[str] = None  # manual | round_robin | load_balanced
    business_hours: Dict[str, Any] = Field(default_factory=dict)
    request_types: List[str] = Field(default_factory=list)
    can_distribute: bool = False            # caller may run round-robin distribution
    # queue totals (active = non-terminal unless stated)
    queue: int = 0                          # total active in scope
    unassigned: int = 0                     # routed to the team, no owner
    breached_active: int = 0
    due_4h: int = 0
    idle_24h: int = 0
    escalated: int = 0
    pending_customer: int = 0
    pending_vendor: int = 0
    on_hold: int = 0
    critical: int = 0
    reopened_active: int = 0
    resolved_today: int = 0
    by_priority: Dict[str, int] = Field(default_factory=dict)
    by_status: Dict[str, int] = Field(default_factory=dict)
    # people + physics
    roster: List[TeamRosterEntry] = Field(default_factory=list)
    flow: List[TeamFlowBucket] = Field(default_factory=list)
    mttr_p50_7d: Optional[float] = None     # minutes, pause-credited
    mttr_p90_7d: Optional[float] = None
    frt_p50_7d: Optional[float] = None      # first-response minutes
    leaderboard: List[TeamLeaderEntry] = Field(default_factory=list)
    hotspots: List[TeamHotspot] = Field(default_factory=list)
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)
    generated_at: Optional[datetime] = None


class TicketHandoff(BaseModel):
    """First-class agent→agent transfer (audited, reason-coded). The target must be in
    the caller's reach — enforced server-side exactly like /assign."""
    to_agent_id: UUID
    reason_code: Optional[str] = None       # HANDOFF_REASON_CODES
    note: Optional[str] = None


class TeamDistributeRequest(BaseModel):
    """Round-robin / load-balanced distribution of the team's unowned queue —
    team-lead or superuser only."""
    team_id: UUID
    max_tickets: int = 25


class TeamDistributeAssignment(BaseModel):
    ticket_id: UUID
    ticket_number: Optional[str] = None
    agent_id: UUID
    agent_name: Optional[str] = None


class TeamDistributeResult(BaseModel):
    assigned: int = 0
    skipped: int = 0
    method: Optional[str] = None
    assignments: List[TeamDistributeAssignment] = Field(default_factory=list)


# ─────────────────────────── Chrono Desk (calendar) ───────────────────────────
class CalendarEvent(BaseModel):
    """One dated occurrence on the agent calendar. A single ticket can emit several
    events of different kinds (a resolution deadline, a hold auto-resume, a cadence
    update...). `id` is the ticket id except for kind='reminder', where it is the
    reminder id and `ticket_id` still points at the ticket."""
    id: UUID
    ticket_id: Optional[UUID] = None
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    kind: str
    at: datetime
    is_breached: bool = False
    sla_state: Optional[str] = None          # ok | due-soon | breached | met
    assigned_agent_id: Optional[UUID] = None
    assigned_agent_name: Optional[str] = None
    team_id: Optional[UUID] = None
    is_major_incident: bool = False
    note: Optional[str] = None               # reminder note
    done: Optional[bool] = None              # reminder done flag


class CalendarDay(BaseModel):
    """Zero-filled per-day bucket, keyed on the CALLER's local date (tz_offset)."""
    date: str                                # YYYY-MM-DD in the caller's zone
    counts: Dict[str, int] = Field(default_factory=dict)
    load: int = 0                            # forward-looking workload (due-kind events)
    breach: int = 0                          # events already breached/overdue that day


class CalendarHoliday(BaseModel):
    date: str
    name: str
    holiday_type: Optional[str] = None


class CalendarBusiness(BaseModel):
    """Business-hours banding source — the caller's first team that declares hours."""
    tz: Optional[str] = None
    days: List[int] = Field(default_factory=list)   # 0=Mon … 6=Sun
    start: Optional[str] = None                     # "09:00"
    end: Optional[str] = None                       # "18:00"
    team_name: Optional[str] = None


class CalendarMeta(BaseModel):
    total_events: int = 0
    truncated: bool = False
    due_today: int = 0
    breach_risk_7d: int = 0                  # due within the next 7 days, not yet met
    breached_open: int = 0                   # already breached and still active in range
    holds_resuming: int = 0
    reminders: int = 0
    busiest_day: Optional[str] = None
    busiest_count: int = 0
    overloaded_days: List[str] = Field(default_factory=list)
    next_open_day: Optional[str] = None      # first future day in range with no load


class CalendarFeedResponse(BaseModel):
    events: List[CalendarEvent] = Field(default_factory=list)
    days: List[CalendarDay] = Field(default_factory=list)
    holidays: List[CalendarHoliday] = Field(default_factory=list)
    business: Optional[CalendarBusiness] = None
    meta: CalendarMeta = Field(default_factory=CalendarMeta)


class ReminderCreate(BaseModel):
    ticket_id: UUID
    remind_at: datetime
    note: Optional[str] = None


class ReminderUpdate(BaseModel):
    remind_at: Optional[datetime] = None
    note: Optional[str] = None
    done: Optional[bool] = None


class ReminderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    remind_at: datetime
    note: Optional[str] = None
    done: bool = False
    created_at: Optional[datetime] = None
    # attached by the router (not ORM columns)
    ticket_number: Optional[str] = None
    subject: Optional[str] = None


# ─────────────────────────── Public portal ───────────────────────────
class PublicTicketCreate(BaseModel):
    """Submission from the no-auth client portal — org code + email gate it."""
    org_code: str
    email: str
    subject: str
    description: Optional[str] = None
    priority: str = "medium"
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class PublicCommentCreate(BaseModel):
    body: str
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
