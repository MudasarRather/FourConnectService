"""Support Desk — Incident Management schemas (Fault Grid / Command Funnel desks).

The incident stats/list/timeline shapes power BOTH panels' dashboards (the seal
does the scoping); the PIR shapes drive the post-incident-report lifecycle.
Field names are a frozen contract with useSupportDesk.js — add, never rename.
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.support_desk.constants import (
    RootCauseCategory, DECISION_KINDS, INCIDENT_TASK_STATUSES,
)


# ─────────────────────────────── Rows & lists ───────────────────────────────
class IncidentRow(BaseModel):
    """One incident on a board — a ticket serialized through the incident lens."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_number: str
    subject: str
    status: str
    priority: str
    sev: int = 4                                  # derived — ticket_sev()
    is_major_incident: bool = False
    ticket_type: Optional[str] = None
    category_id: Optional[UUID] = None
    category_name: Optional[str] = None
    team_id: Optional[UUID] = None
    team_name: Optional[str] = None
    assigned_agent_id: Optional[UUID] = None
    assigned_agent_name: Optional[str] = None
    incident_commander_id: Optional[UUID] = None
    incident_commander_name: Optional[str] = None
    comms_lead_id: Optional[UUID] = None
    comms_lead_name: Optional[str] = None
    ops_lead_id: Optional[UUID] = None
    ops_lead_name: Optional[str] = None
    # Owner-tier signal for the boards: the named collaborators invited to work this
    # incident. Lets the desk UI gate command verbs to assignee/collaborator/lead/admin
    # (mirrors the drawer's canCommand) instead of showing buttons that 403 on click.
    collaborators: List[UUID] = Field(default_factory=list)
    affected_services: List[str] = Field(default_factory=list)
    affected_users: Optional[int] = None
    business_impact: Optional[str] = None
    revenue_impact: Optional[str] = None
    compliance_impact: bool = False
    security_impact: bool = False
    public_impact: bool = False
    war_room_url: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    update_interval_minutes: Optional[int] = None
    next_update_due_at: Optional[datetime] = None
    last_status_update_at: Optional[datetime] = None
    is_escalated: bool = False
    escalation_level: int = 0
    response_due_at: Optional[datetime] = None
    resolution_due_at: Optional[datetime] = None
    first_responded_at: Optional[datetime] = None
    sla_response_breached: bool = False
    sla_resolution_breached: bool = False
    sla_paused_since: Optional[datetime] = None
    rca_summary: Optional[str] = None
    linked_problem_id: Optional[UUID] = None
    incident_started_at: Optional[datetime] = None
    incident_detected_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    created_at: datetime
    has_pir: bool = False
    pir_id: Optional[UUID] = None
    pir_status: Optional[str] = None
    # parent/child linking (one level deep): child rows carry their master's id/number,
    # master rows carry a live rollup count.
    parent_incident_id: Optional[UUID] = None
    parent_incident_number: Optional[str] = None
    child_count: int = 0
    # MI-candidate proposal (propose → confirm/decline). Set only while a proposal
    # is pending; cleared on confirm/decline/direct-declare.
    mi_proposed_at: Optional[datetime] = None
    mi_proposed_by_id: Optional[UUID] = None
    mi_proposed_by_name: Optional[str] = None
    mi_proposal_note: Optional[str] = None
    # Response-playbook rollup (additive): task_total counts NON-skipped tasks
    # (open + done — skipped is the tombstone and never counts against progress),
    # so task_done/task_total matches the task list's progress_pct semantics.
    task_total: int = 0
    task_done: int = 0


class IncidentListResponse(BaseModel):
    total: int = 0
    page: int = 1
    limit: int = 100
    items: List[IncidentRow] = Field(default_factory=list)


# ─────────────────────────────── Dashboard stats ───────────────────────────────
class IncidentSlaSplit(BaseModel):
    met: int = 0
    breached: int = 0
    at_risk: int = 0


class IncidentCategorySlice(BaseModel):
    key: Optional[str] = None
    label: str = "Uncategorised"
    count: int = 0
    breached: int = 0


class IncidentServiceSlice(BaseModel):
    service: str
    count: int = 0
    open: int = 0
    sev12: int = 0


class IncidentTrendPoint(BaseModel):
    day: date
    created: int = 0
    resolved: int = 0


class IncidentFeedItem(BaseModel):
    at: datetime
    action: str
    actor: Optional[str] = None
    ticket_id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    sev: int = 4


class IncidentPirCounts(BaseModel):
    draft: int = 0
    in_review: int = 0
    approved: int = 0
    published: int = 0
    missing: int = 0          # legacy: terminal MAJOR incidents w/o PIR, all-time
    owed: int = 0             # v2 single truth: SEV1∪SEV2 terminal w/o PIR, 90d window
    actions_open: int = 0     # open+in_progress follow-through items (approved/published)


class IncidentExposureSlice(BaseModel):
    """Exposure rollup over the LIVE critical lens. Chip ⇔ rows lockstep: compliance/
    security/public/revenue_flagged/unassessed each equal the total of
    GET /incidents/?lens=critical&live=1&flag=exposure_*|unassessed."""
    by_business_impact: Dict[str, int] = Field(default_factory=dict)  # {"low":..,"critical":..}
    compliance: int = 0
    security: int = 0
    public: int = 0
    revenue_flagged: int = 0
    unassessed: int = 0


class IncidentPlaybookCounts(BaseModel):
    tickets_with_tasks: int = 0
    tasks_open: int = 0
    tasks_done: int = 0


class IncidentResponderLoad(BaseModel):
    user_id: UUID
    name: Optional[str] = None
    sev1: int = 0
    sev2: int = 0
    unacked: int = 0


class IncidentCriticalStats(BaseModel):
    """Critical-desk rollup (SEV1 ∪ SEV2), computed LIVE-ONLY — lockstep with the rows
    GET /incidents/?lens=critical&live=1&… returns: sev2_unacked/update_overdue/at_risk/
    breached/unowned each ⇔ ...&sev=2&flag=<name>.

    Flag-name translation for chips living OUTSIDE this block (documented in the
    useSupportDesk twin too): flag cmdr_unstaffed ⇔ stats.roles_unassigned ·
    flag mi_proposed ⇔ stats.mi_proposals_pending · flag at_risk/breached ⇔ stats.sla.*"""
    sev1_active: int = 0
    sev2_active: int = 0
    sev2_unacked: int = 0
    sev2_update_overdue: int = 0
    sev2_at_risk: int = 0
    sev2_breached: int = 0
    sev2_unowned: int = 0
    ack_coverage_pct: Optional[float] = None       # acked share of live SEV1∪SEV2
    oldest_sev2_age_minutes: Optional[float] = None
    exposure: IncidentExposureSlice = Field(default_factory=IncidentExposureSlice)
    # 30d activity counts (sealed ticket join, like the feed)
    mi_proposed_30d: int = 0
    mi_confirmed_30d: int = 0
    mi_declined_30d: int = 0
    de_escalations_30d: int = 0                    # incident_sev_changed → SEV3
    playbook: IncidentPlaybookCounts = Field(default_factory=IncidentPlaybookCounts)
    responder_load: List[IncidentResponderLoad] = Field(default_factory=list)


class IncidentStatsResponse(BaseModel):
    active_total: int = 0
    new_today: int = 0
    resolved_today: int = 0
    by_sev: Dict[str, int] = Field(default_factory=dict)   # {"sev1":.., .., "sev4":..}
    major_active: int = 0
    unacked: int = 0
    unowned: int = 0
    update_overdue: int = 0
    roles_unassigned: int = 0
    mtta_minutes_30d: Optional[float] = None
    mttr_minutes_current_month: Optional[float] = None
    mttr_minutes_prev_month: Optional[float] = None
    mttr_trend_pct: Optional[float] = None                  # negative = improving
    sla: IncidentSlaSplit = Field(default_factory=IncidentSlaSplit)
    by_category: List[IncidentCategorySlice] = Field(default_factory=list)
    top_services: List[IncidentServiceSlice] = Field(default_factory=list)
    trend_14d: List[IncidentTrendPoint] = Field(default_factory=list)
    feed: List[IncidentFeedItem] = Field(default_factory=list)
    missing_rca: int = 0
    pir: IncidentPirCounts = Field(default_factory=IncidentPirCounts)
    # Phase-clock analytics (all additive): MTTD = started→detected over 30d where
    # both clocks were stamped; phase_minutes_30d = {"detect_to_ack", "ack_to_resolve"}.
    mttd_minutes_30d: Optional[float] = None
    phase_minutes_30d: Dict[str, float] = Field(default_factory=dict)
    # MI-candidate docket + PIR action-item follow-through.
    mi_proposals_pending: int = 0
    actions_overdue: int = 0
    # Critical-desk rollup (additive) — see IncidentCriticalStats for lockstep rules.
    critical: IncidentCriticalStats = Field(default_factory=IncidentCriticalStats)


# ─────────────────────────────── Cross-incident timeline ───────────────────────────────
class IncidentTimelineEvent(BaseModel):
    at: datetime
    action: str
    actor: Optional[str] = None
    detail: Dict[str, Any] = Field(default_factory=dict)
    ticket_id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    sev: int = 4
    status: Optional[str] = None
    # Additive enrichment (Incident Timeline rebuild): stable event identity +
    # catalog meta + pin state. Old consumers ignore these safely.
    id: Optional[UUID] = None
    category: Optional[str] = None
    label: Optional[str] = None
    is_milestone: bool = False
    actor_user_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    # Owner identity of the parent incident — lets the timeline streams gate the
    # PIN/curate affordance on actor-tier client-side (mirrors _require_ticket_actor)
    # instead of showing it to every in-scope viewer and 403-ing on click. Additive.
    assigned_agent_id: Optional[UUID] = None
    incident_commander_id: Optional[UUID] = None


class IncidentTimelineDay(BaseModel):
    day: date
    events: List[IncidentTimelineEvent] = Field(default_factory=list)


class IncidentTimelineResponse(BaseModel):
    total: int = 0
    page: int = 1
    limit: int = 100
    days: List[IncidentTimelineDay] = Field(default_factory=list)
    # Live-ticker cursor: "<created_at ISO>~<activity uuid>" of the newest event this
    # response knows about; pass back as ?since= for incremental arrivals.
    cursor: Optional[str] = None


# ─────────────────────────────── Command verbs ───────────────────────────────
class IncidentRolesPatch(BaseModel):
    """None = leave untouched; explicit null clears (mirrors PATCH semantics via sentinel).
    ``note`` is the handoff reason — REQUIRED (enforced in the router) whenever a change
    replaces or stands down someone already holding the seat, optional for fresh staffing."""
    incident_commander_id: Optional[UUID] = None
    comms_lead_id: Optional[UUID] = None
    ops_lead_id: Optional[UUID] = None
    clear: List[str] = Field(default_factory=list)   # e.g. ["comms_lead_id"]
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("clear")
    @classmethod
    def _known_clear(cls, v):
        allowed = {"incident_commander_id", "comms_lead_id", "ops_lead_id"}
        bad = [x for x in v if x not in allowed]
        if bad:
            raise ValueError(f"Unknown role field(s): {', '.join(bad)}")
        return v


class IncidentImpactPatch(BaseModel):
    """``note`` is the assessment reason — REQUIRED (enforced in the router) whenever a
    change REVISES a value that was already stamped; the first stamp of a field is free."""
    affected_services: Optional[List[str]] = None
    incident_started_at: Optional[datetime] = None
    incident_detected_at: Optional[datetime] = None
    compliance_impact: Optional[bool] = None
    security_impact: Optional[bool] = None
    public_impact: Optional[bool] = None
    business_impact: Optional[str] = Field(default=None, max_length=40)
    affected_users: Optional[int] = Field(default=None, ge=0, le=100_000_000)
    revenue_impact: Optional[str] = Field(default=None, max_length=160)
    note: Optional[str] = Field(default=None, max_length=500)

    @field_validator("affected_services")
    @classmethod
    def _trim_services(cls, v):
        if v is None:
            return v
        out = [str(s).strip()[:120] for s in v if str(s or "").strip()]
        return out[:20]

    @field_validator("business_impact")
    @classmethod
    def _known_impact(cls, v):
        if v is None or not str(v).strip():
            return None
        v = str(v).strip().lower()
        allowed = {"low", "medium", "high", "critical"}
        if v not in allowed:
            raise ValueError(f"business_impact must be one of: {', '.join(sorted(allowed))}")
        return v


class IncidentParentPatch(BaseModel):
    """Link this incident under a master incident, or clear the link. Exactly one of
    parent_id / clear must be meaningful — parent_id wins when both are sent."""
    parent_id: Optional[UUID] = None
    clear: bool = False
    # Structured "why" — recorded on the link/unlink activity rows so the rollup
    # decision carries its rationale. Additive + optional.
    note: Optional[str] = Field(default=None, max_length=300)


class IncidentDecisionCreate(BaseModel):
    kind: str = "other"                    # DecisionKind value
    decision: str = Field(min_length=3, max_length=600)
    # Structured "why" — a per-kind preset (or free text) recorded beside the free-form
    # note so the ledger row carries a scannable rationale. Additive + optional.
    reason: Optional[str] = Field(default=None, max_length=300)
    note: Optional[str] = Field(default=None, max_length=2000)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v):
        if v not in DECISION_KINDS:
            raise ValueError(f"kind must be one of: {', '.join(DECISION_KINDS)}")
        return v


# ─────────────────────────────── Response playbooks / incident tasks ───────────────────────────────
class IncidentTaskItem(BaseModel):
    """One response task on an incident (see SdIncidentTask). owner/done_by names are
    router enrichment, not ORM columns."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    seq: int = 0
    title: str
    note: Optional[str] = None
    owner_id: Optional[UUID] = None
    owner_name: Optional[str] = None
    status: str = "open"                    # INCIDENT_TASK_STATUSES
    status_note: Optional[str] = None
    done_at: Optional[datetime] = None
    done_by_id: Optional[UUID] = None
    done_by_name: Optional[str] = None
    template_key: Optional[str] = None
    created_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class IncidentTaskListResponse(BaseModel):
    """progress_pct = done / (open + done) — skipped rows never count against progress
    (they're the tombstone, not unfinished work). None when there's nothing countable."""
    total: int = 0
    open: int = 0
    done: int = 0
    skipped: int = 0
    progress_pct: Optional[float] = None
    items: List[IncidentTaskItem] = Field(default_factory=list)


class IncidentTaskCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    note: Optional[str] = Field(default=None, max_length=1000)
    owner_id: Optional[UUID] = None


class IncidentTaskPatch(BaseModel):
    """Transitions: open→done free · open→skipped needs status_note · done→open needs a
    correction note (status_note, ``note`` accepted as fallback) · skipped→open free ·
    done→skipped 422 · same-status 422. ``clear_owner`` unstaffs; ``note``/``title``
    edit the task body. All rules enforced in the router."""
    status: Optional[str] = None
    status_note: Optional[str] = Field(default=None, max_length=300)
    owner_id: Optional[UUID] = None
    clear_owner: bool = False
    note: Optional[str] = Field(default=None, max_length=1000)
    title: Optional[str] = Field(default=None, min_length=3, max_length=300)

    @field_validator("status")
    @classmethod
    def _known_status(cls, v):
        if v is None:
            return v
        v = str(v).strip().lower()
        if v not in INCIDENT_TASK_STATUSES:
            raise ValueError("status must be one of: " + ", ".join(INCIDENT_TASK_STATUSES))
        return v


class PlaybookApplyRequest(BaseModel):
    template_key: str = Field(min_length=1, max_length=60)


class IncidentSevChange(BaseModel):
    """Severity reclassification: target_sev=2 promotes to priority 'critical'
    (owner-tier — raising the alarm is safe to over-do); target_sev=3 de-escalates to
    'high' (lead/superuser — removing the desk's eyes carries the decline-an-MI bar).
    SEV1 moves stay on the major-incident verbs. The note is the case — mandatory."""
    target_sev: int
    note: str = Field(min_length=10, max_length=500)

    @field_validator("target_sev")
    @classmethod
    def _known_sev(cls, v):
        if v not in (2, 3):
            raise ValueError("target_sev must be 2 (promote to critical) or 3 (de-escalate "
                             "to high) — SEV1 is the major-incident flag, use the MI verbs")
        return v


class SimilarIncidentItem(BaseModel):
    id: UUID
    ticket_number: str
    subject: str
    sev: int = 4
    resolved_at: Optional[datetime] = None
    resolution_summary: Optional[str] = None
    rca_summary: Optional[str] = None
    root_cause_hint: Optional[str] = None   # resolution_category
    score: float = 0.0
    reason: Optional[str] = None            # why it matched (category/service/keyword)


# ─────────────────────────────── PIR lifecycle ───────────────────────────────
PIR_ACTION_STATUSES = ("open", "in_progress", "done")


class PirActionItem(BaseModel):
    aid: Optional[str] = Field(default=None, max_length=16)   # stable address (server-assigned)
    action: str = Field(min_length=3, max_length=400)
    owner_id: Optional[UUID] = None
    owner_name: Optional[str] = None
    target_date: Optional[date] = None
    status: str = "open"                    # open|in_progress|done

    @field_validator("status")
    @classmethod
    def _known_action_status(cls, v):
        v = str(v or "open").strip().lower()
        if v not in PIR_ACTION_STATUSES:
            raise ValueError("status must be 'open', 'in_progress' or 'done'")
        return v


class PirParticipant(BaseModel):
    user_id: Optional[UUID] = None
    name: str = Field(min_length=1, max_length=120)
    role: Optional[str] = Field(default=None, max_length=60)


class PirCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)


class PirUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)
    executive_summary: Optional[str] = None
    business_impact: Optional[str] = None
    technical_impact: Optional[str] = None
    root_cause: Optional[str] = None
    root_cause_category: Optional[str] = None
    five_whys: Optional[List[str]] = None
    corrective_actions: Optional[List[PirActionItem]] = None
    preventive_actions: Optional[List[PirActionItem]] = None
    lessons_learned: Optional[str] = None
    refresh_timeline: bool = False          # re-snapshot the activity trail
    # ── v2 parity pack ──
    contributing_factors: Optional[List[str]] = None    # ≤10×240 tag strings
    went_well: Optional[List[str]] = None               # blameless retro (≤10×300)
    went_wrong: Optional[List[str]] = None
    participants: Optional[List[PirParticipant]] = None  # ≤20
    review_meeting_at: Optional[datetime] = None         # explicit null CLEARS (whitelisted)
    review_meeting_notes: Optional[str] = Field(default=None, max_length=2000)
    refresh_metrics: bool = False           # re-freeze the metrics snapshot (draft/in_review)

    @field_validator("root_cause_category")
    @classmethod
    def _known_cat(cls, v):
        if v is None or v == "":
            return None
        allowed = {c.value for c in RootCauseCategory}
        if v not in allowed:
            raise ValueError(f"root_cause_category must be one of: {', '.join(sorted(allowed))}")
        return v

    @field_validator("five_whys")
    @classmethod
    def _cap_whys(cls, v):
        if v is None:
            return v
        return [str(w).strip()[:600] for w in v][:5]

    @field_validator("contributing_factors")
    @classmethod
    def _cap_factors(cls, v):
        if v is None:
            return v
        return [str(f).strip()[:240] for f in v if str(f).strip()][:10]

    @field_validator("went_well", "went_wrong")
    @classmethod
    def _cap_retro(cls, v):
        if v is None:
            return v
        return [str(r).strip()[:300] for r in v if str(r).strip()][:10]

    @field_validator("participants")
    @classmethod
    def _cap_participants(cls, v):
        if v is None:
            return v
        return v[:20]


class PirReview(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)


class PirResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    report_number: str
    title: str
    status: str
    executive_summary: Optional[str] = None
    business_impact: Optional[str] = None
    technical_impact: Optional[str] = None
    timeline_snapshot: List[Dict[str, Any]] = Field(default_factory=list)
    root_cause: Optional[str] = None
    root_cause_category: Optional[str] = None
    five_whys: List[str] = Field(default_factory=list)
    corrective_actions: List[Dict[str, Any]] = Field(default_factory=list)
    preventive_actions: List[Dict[str, Any]] = Field(default_factory=list)
    lessons_learned: Optional[str] = None
    # ── v2 parity pack ──
    metrics_snapshot: Optional[Dict[str, Any]] = None
    contributing_factors: List[str] = Field(default_factory=list)
    went_well: List[str] = Field(default_factory=list)
    went_wrong: List[str] = Field(default_factory=list)
    participants: List[Dict[str, Any]] = Field(default_factory=list)
    review_meeting_at: Optional[datetime] = None
    review_meeting_notes: Optional[str] = None
    revisions: List[Dict[str, Any]] = Field(default_factory=list)
    distribution: Optional[Dict[str, Any]] = None
    approvals: List[Dict[str, Any]] = Field(default_factory=list)
    submitted_at: Optional[datetime] = None
    submitted_by_id: Optional[UUID] = None   # the four-eyes check key (reviewer ≠ submitter)
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    created_by_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    # enrichment (filled by the router, not the ORM)
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    sev: int = 4
    created_by_name: Optional[str] = None
    submitted_by_name: Optional[str] = None
    incident_commander_name: Optional[str] = None
    # Owner identity of the linked incident (filled by the router) — lets the Glass
    # Dossier builder gate edit/submit on actor-tier (assignee/commander/collaborator/
    # lead/admin, mirrors _require_ticket_actor) instead of showing controls that 403.
    assigned_agent_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    incident_commander_id: Optional[UUID] = None
    collaborators: List[UUID] = Field(default_factory=list)


class PirListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticket_id: UUID
    report_number: str
    title: str
    status: str
    submitted_at: Optional[datetime] = None
    submitted_by_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    review_meeting_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    sev: int = 4
    created_by_name: Optional[str] = None


class PirListResponse(BaseModel):
    total: int = 0
    items: List[PirListItem] = Field(default_factory=list)


# ─────────────────────────────── PIR board (sealed, lockstep) ───────────────────────────────
class PirBoardItem(BaseModel):
    """One board row. kind='pir' rows carry the report fields; kind='owed' rows are
    tickets WITHOUT a report yet (the debt lens) — pir_* fields stay None there.
    Both shapes share the ticket block so the desk renders one row component."""
    kind: str = "pir"                        # pir | owed
    # report block (kind='pir')
    pir_id: Optional[UUID] = None
    report_number: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None             # PIR status
    submitted_at: Optional[datetime] = None
    submitted_by_id: Optional[UUID] = None
    submitted_by_name: Optional[str] = None
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    review_meeting_at: Optional[datetime] = None
    created_by_id: Optional[UUID] = None
    created_by_name: Optional[str] = None
    updated_at: Optional[datetime] = None
    actions_total: int = 0
    actions_done: int = 0
    actions_overdue: int = 0
    has_metrics: bool = False
    # ticket block (both kinds)
    ticket_id: UUID
    ticket_number: str
    subject: str
    sev: int = 4
    is_major_incident: bool = False
    ticket_status: Optional[str] = None
    team_id: Optional[UUID] = None
    team_name: Optional[str] = None
    assigned_agent_id: Optional[UUID] = None
    assigned_agent_name: Optional[str] = None
    incident_commander_id: Optional[UUID] = None
    incident_commander_name: Optional[str] = None
    # named collaborators on the incident — the desk gates "Open Review" on actor-tier
    collaborators: List[UUID] = Field(default_factory=list)
    terminal_at: Optional[datetime] = None    # resolved/closed stamp (owed aging)
    age_days: Optional[int] = None            # owed: days since terminal stamp


class PirBoardStats(BaseModel):
    """Lockstep with the lenses — a chip's number always equals its click's rows."""
    owed: int = 0
    draft: int = 0
    in_review: int = 0
    approved: int = 0
    published: int = 0
    actions_open: int = 0
    actions_overdue: int = 0
    actions_due: int = 0                      # PIRs with ≥1 overdue action (the lens count)
    coverage_pct: Optional[float] = None      # reviewed closures / owed-eligible (90d)
    median_review_hours_30d: Optional[float] = None
    published_30d: int = 0
    meetings_upcoming: int = 0


class PirBoardResponse(BaseModel):
    total: int = 0
    page: int = 1
    limit: int = 50
    lens: str = "all"
    stats: PirBoardStats = Field(default_factory=PirBoardStats)
    items: List[PirBoardItem] = Field(default_factory=list)
    generated_at: Optional[datetime] = None


# ─────────────────────────────── Phase clocks ───────────────────────────────
class PhasePoint(BaseModel):
    key: str
    label: str
    at: Optional[datetime] = None
    source: str = "ticket"                 # ticket | activity | created_at


class IncidentPhasesResponse(BaseModel):
    ticket_id: UUID
    ticket_number: str
    sev: int = 4
    phases: List[PhasePoint] = Field(default_factory=list)
    durations_minutes: Dict[str, float] = Field(default_factory=dict)
    mttd_minutes: Optional[float] = None    # started → detected
    mtta_minutes: Optional[float] = None    # detected → acknowledged
    mttr_minutes: Optional[float] = None    # detected → resolved


# ─────────────────────────────── MI proposal workflow ───────────────────────────────
class MiProposalCreate(BaseModel):
    """Owner-tier agents PROPOSE major status; a team lead / superuser confirms or
    declines. The note is the case for severity — mandatory and substantial."""
    note: str = Field(min_length=10, max_length=500)
    business_impact: Optional[str] = Field(default=None, max_length=40)
    affected_users: Optional[int] = Field(default=None, ge=0, le=100_000_000)

    @field_validator("business_impact")
    @classmethod
    def _known_impact(cls, v):
        if v is None or not str(v).strip():
            return None
        v = str(v).strip().lower()
        allowed = {"low", "medium", "high", "critical"}
        if v not in allowed:
            raise ValueError(f"business_impact must be one of: {', '.join(sorted(allowed))}")
        return v


class MiProposalDecision(BaseModel):
    """Confirm: note optional, cadence/war-room arm optional. Decline: note REQUIRED
    (enforced in the router with a clearer message than a schema error)."""
    note: Optional[str] = Field(default=None, max_length=500)
    update_interval_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    open_war_room: bool = False


# ─────────────────────────────── PIR action-item tracker ───────────────────────────────
class IncidentActionRow(BaseModel):
    """One corrective/preventive action item, flattened out of its PIR register.
    Addressed by (pir_id, kind, index) — the PATCH route's coordinates; ``aid`` is
    the stable per-item address that survives draft-era reorders (prefer it)."""
    pir_id: UUID
    report_number: str
    pir_status: str
    ticket_id: UUID
    ticket_number: str
    subject: str
    sev: int = 4
    kind: str                               # corrective | preventive
    index: int
    aid: Optional[str] = None
    action: str
    owner_id: Optional[UUID] = None
    owner_name: Optional[str] = None
    target_date: Optional[str] = None       # ISO date string as stored in JSONB
    status: str = "open"
    overdue: bool = False
    status_changed_at: Optional[str] = None
    status_changed_by: Optional[str] = None
    status_note: Optional[str] = None


class IncidentActionCounts(BaseModel):
    open: int = 0            # open + in_progress (anything not done)
    in_progress: int = 0     # the working subset of `open`
    done: int = 0
    overdue: int = 0


class IncidentActionsResponse(BaseModel):
    total: int = 0
    page: int = 1
    limit: int = 50
    counts: IncidentActionCounts = Field(default_factory=IncidentActionCounts)
    items: List[IncidentActionRow] = Field(default_factory=list)


class PirActionStatusPatch(BaseModel):
    """Status-only carve-out: the one mutation allowed on an approved/published PIR.
    The document stays sealed; only per-action status (+ additive audit keys) moves.
    ``aid`` (when sent) is the authoritative address — if the row at ``index`` moved,
    the server re-resolves by aid instead of silently patching the wrong item."""
    status: str
    note: Optional[str] = Field(default=None, max_length=300)
    aid: Optional[str] = Field(default=None, max_length=16)

    @field_validator("status")
    @classmethod
    def _known_status(cls, v):
        v = str(v or "").strip().lower()
        if v not in PIR_ACTION_STATUSES:
            raise ValueError("status must be 'open', 'in_progress' or 'done'")
        return v


# ─────────────────────────────── Executive sitrep ───────────────────────────────
class SitrepRoster(BaseModel):
    commander_id: Optional[UUID] = None
    commander_name: Optional[str] = None
    comms_lead_id: Optional[UUID] = None
    comms_lead_name: Optional[str] = None
    ops_lead_id: Optional[UUID] = None
    ops_lead_name: Optional[str] = None


class SitrepImpact(BaseModel):
    affected_services: List[str] = Field(default_factory=list)
    affected_users: Optional[int] = None
    business_impact: Optional[str] = None
    revenue_impact: Optional[str] = None
    compliance_impact: bool = False
    security_impact: bool = False
    public_impact: bool = False


class SitrepCadence(BaseModel):
    interval_minutes: Optional[int] = None
    next_due_at: Optional[datetime] = None
    last_update_at: Optional[datetime] = None
    overdue: bool = False


class SitrepLastUpdate(BaseModel):
    at: Optional[datetime] = None
    actor: Optional[str] = None
    phase: Optional[str] = None
    audience: Optional[str] = None
    preview: Optional[str] = None


class SitrepDecisionItem(BaseModel):
    at: Optional[datetime] = None
    kind: Optional[str] = None
    decision: Optional[str] = None
    actor: Optional[str] = None


class SitrepDecisions(BaseModel):
    count: int = 0
    latest: List[SitrepDecisionItem] = Field(default_factory=list)


class SitrepSla(BaseModel):
    response_due_at: Optional[datetime] = None
    resolution_due_at: Optional[datetime] = None
    sla_response_breached: bool = False
    sla_resolution_breached: bool = False
    sla_paused_since: Optional[datetime] = None


class SitrepChildren(BaseModel):
    count: int = 0
    open_count: int = 0


class SitrepPir(BaseModel):
    id: Optional[UUID] = None
    report_number: Optional[str] = None
    status: Optional[str] = None


class IncidentSitrepResponse(BaseModel):
    ticket_id: UUID
    ticket_number: str
    subject: str
    status: str
    sev: int = 4
    is_major_incident: bool = False
    running: Optional[str] = None           # "2h 03m" human clock (detected → now/resolved)
    generated_at: datetime
    phases: List[PhasePoint] = Field(default_factory=list)
    durations_minutes: Dict[str, float] = Field(default_factory=dict)
    roster: SitrepRoster = Field(default_factory=SitrepRoster)
    impact: SitrepImpact = Field(default_factory=SitrepImpact)
    cadence: SitrepCadence = Field(default_factory=SitrepCadence)
    last_update: SitrepLastUpdate = Field(default_factory=SitrepLastUpdate)
    decisions: SitrepDecisions = Field(default_factory=SitrepDecisions)
    sla: SitrepSla = Field(default_factory=SitrepSla)
    children: SitrepChildren = Field(default_factory=SitrepChildren)
    watchers_total: int = 0
    pir: SitrepPir = Field(default_factory=SitrepPir)
    war_room_url: Optional[str] = None


# ═══════════════════════ Incident Timeline rebuild — new surfaces ═══════════════════════

class TimelineCatalogEntry(BaseModel):
    """One registered activity action with its read-side meta (ACTIVITY_CATALOG)."""
    action: str
    label: str
    category: str
    tone: str = "dim"
    milestone_eligible: bool = False
    system: bool = False


class TimelineCatalogResponse(BaseModel):
    categories: List[str] = Field(default_factory=list)
    actions: List[TimelineCatalogEntry] = Field(default_factory=list)
    milestone_cap: int = 12
    milestone_eligible: List[str] = Field(default_factory=list)


class TimelinePulseDensityPoint(BaseModel):
    at: datetime          # bucket start, caller-local (tz_offset applied server-side)
    count: int = 0


class TimelinePulseActor(BaseModel):
    actor_user_id: Optional[UUID] = None
    name: Optional[str] = None
    count: int = 0


class TimelinePulseFlow(BaseModel):
    created: int = 0
    resolved: int = 0


class TimelinePulseBusiest(BaseModel):
    ticket_id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    sev: int = 4
    events: int = 0


class TimelinePulseTeam(BaseModel):
    team_id: Optional[UUID] = None
    team_name: Optional[str] = None
    count: int = 0


class TimelinePulseResponse(BaseModel):
    """Window-scoped aggregates for the timeline hero instruments. Sealed like the feed."""
    from_at: datetime
    to_at: datetime
    tz_offset: int = 0
    bucket: str = "day"                     # "hour" when the window ≤ 48h
    total_events: int = 0
    density: List[TimelinePulseDensityPoint] = Field(default_factory=list)
    by_category: Dict[str, int] = Field(default_factory=dict)
    by_sev: Dict[str, int] = Field(default_factory=dict)
    milestones: int = 0
    system_events: int = 0
    human_events: int = 0
    top_actors: List[TimelinePulseActor] = Field(default_factory=list)
    flow: TimelinePulseFlow = Field(default_factory=TimelinePulseFlow)
    mtta_minutes: Optional[float] = None
    mttr_minutes: Optional[float] = None
    busiest: Optional[TimelinePulseBusiest] = None
    by_team: List[TimelinePulseTeam] = Field(default_factory=list)


class TimelinePinResponse(BaseModel):
    """Pin/unpin verb receipt — the activity row's milestone state after the verb."""
    id: UUID
    ticket_id: UUID
    action: str
    is_milestone: bool = False
    pinned_by_id: Optional[UUID] = None
    pinned_by_name: Optional[str] = None
    pinned_at: Optional[datetime] = None


class IncidentStreamTicket(BaseModel):
    """Row-shaped header block so the peek's verb rail can gate honestly (client
    SdIncVerbRail reads these exact fields off incident rows)."""
    id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[str] = None
    sev: int = 4
    is_major_incident: bool = False
    acknowledged_at: Optional[datetime] = None
    mi_proposed_at: Optional[datetime] = None
    war_room_url: Optional[str] = None
    team_id: Optional[UUID] = None
    assigned_agent_id: Optional[UUID] = None
    incident_commander_id: Optional[UUID] = None
    parent_incident_id: Optional[UUID] = None
    created_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None


class IncidentStreamItem(BaseModel):
    """One entry of the merged per-incident dossier stream."""
    kind: str                               # activity | comment | worklog | task
    id: UUID
    at: datetime
    actor_user_id: Optional[UUID] = None
    actor: Optional[str] = None
    title: Optional[str] = None             # catalog label / comment kind / task title
    body: Optional[str] = None              # comment body / worklog note (None elsewhere)
    category: Optional[str] = None          # catalog category for activities
    tone: Optional[str] = None
    is_internal: bool = False
    is_milestone: bool = False
    meta: Dict[str, Any] = Field(default_factory=dict)


class IncidentStreamCounts(BaseModel):
    activity: int = 0
    comment: int = 0
    worklog: int = 0
    task: int = 0


class IncidentStreamResponse(BaseModel):
    ticket: IncidentStreamTicket
    total: int = 0
    page: int = 1
    limit: int = 50
    counts: IncidentStreamCounts = Field(default_factory=IncidentStreamCounts)
    items: List[IncidentStreamItem] = Field(default_factory=list)


# ═══════════════════════════════ RCA desks (RCA v2) ═══════════════════════════════

class RcaBoardItem(BaseModel):
    """One row of the RCA board — the ticket's RCA story, review state and debt age."""
    ticket_id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    sev: int = 4
    priority: Optional[str] = None
    status: Optional[str] = None
    team_id: Optional[UUID] = None
    team_name: Optional[str] = None
    assigned_agent_id: Optional[UUID] = None
    assigned_agent_name: Optional[str] = None
    is_major_incident: bool = False
    breached: bool = False
    breach_reason: Optional[str] = None
    resolved_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    rca_status: Optional[str] = None            # EFFECTIVE status (legacy rows read 'filed')
    rca_category: Optional[str] = None
    rca_summary_preview: Optional[str] = None   # first 240 chars
    rca_five_whys: List[str] = Field(default_factory=list)
    rca_factors: List[str] = Field(default_factory=list)
    rca_corrective: Optional[str] = None
    rca_preventive: Optional[str] = None
    rca_filed_at: Optional[datetime] = None
    rca_filed_by_id: Optional[UUID] = None
    rca_filed_by_name: Optional[str] = None
    rca_reviewed_at: Optional[datetime] = None
    rca_reviewed_by_id: Optional[UUID] = None
    rca_reviewed_by_name: Optional[str] = None
    rca_review_note: Optional[str] = None
    inherited: bool = False                     # cascade-stamped from a problem
    linked_problem_id: Optional[UUID] = None
    owed_age_hours: Optional[float] = None      # terminal-stamp → now, owed rows only
    can_file: bool = True                       # requester may (re)file this ticket's RCA —
                                                #   owner-tier gate (assignee/collaborator/lead/
                                                #   claim-eligible/swarm/superuser); lets the
                                                #   agent desk only offer FILE where POST /rca
                                                #   will accept it (no fill-then-403)


class RcaBoardAging(BaseModel):
    d0_3: int = 0
    d3_7: int = 0
    d7_14: int = 0
    d14_plus: int = 0


class RcaBoardStats(BaseModel):
    """Lockstep with the board rows — same conditions, same seal, same window."""
    owed: int = 0
    pending: int = 0        # filed, awaiting review
    returned: int = 0
    validated: int = 0
    stale: int = 0
    eligible: int = 0       # coverage denominator (terminal-in-window owing an RCA)
    coverage_pct: int = 100
    aging: RcaBoardAging = Field(default_factory=RcaBoardAging)


class RcaBoardResponse(BaseModel):
    items: List[RcaBoardItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    limit: int = 25
    lens: str = "owed"
    days: int = 30
    stats: RcaBoardStats = Field(default_factory=RcaBoardStats)
    generated_at: Optional[datetime] = None


class RcaMixSlice(BaseModel):
    key: str
    count: int = 0


class RcaLatency(BaseModel):
    median_hours: Optional[float] = None
    p90_hours: Optional[float] = None
    n: int = 0


class RcaActionsFollowThrough(BaseModel):
    total: int = 0
    done: int = 0
    open: int = 0
    overdue: int = 0
    done_pct: int = 100


class RcaKedbStats(BaseModel):
    known_errors: int = 0
    published_workarounds: int = 0
    linked_ticket_total: int = 0


class RcaTrendWeek(BaseModel):
    week_start: Optional[datetime] = None
    filed: int = 0
    validated: int = 0


class RcaCoverage(BaseModel):
    eligible: int = 0
    covered: int = 0
    pct: int = 100


class RcaAnalyticsResponse(BaseModel):
    days: int = 90
    coverage: RcaCoverage = Field(default_factory=RcaCoverage)
    category_mix: List[RcaMixSlice] = Field(default_factory=list)
    breach_reason_mix: List[RcaMixSlice] = Field(default_factory=list)
    cycle_time: RcaLatency = Field(default_factory=RcaLatency)          # resolved → filed
    review_latency: RcaLatency = Field(default_factory=RcaLatency)     # filed → reviewed
    debt_aging: RcaBoardAging = Field(default_factory=RcaBoardAging)
    actions_follow_through: RcaActionsFollowThrough = Field(default_factory=RcaActionsFollowThrough)
    kedb: RcaKedbStats = Field(default_factory=RcaKedbStats)
    trend: List[RcaTrendWeek] = Field(default_factory=list)
    generated_at: Optional[datetime] = None


class RcaClusterTicket(BaseModel):
    id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    sev: int = 4
    resolved_at: Optional[datetime] = None
    rca_status: Optional[str] = None


class RcaClusterSignature(BaseModel):
    category_id: Optional[UUID] = None
    category_name: Optional[str] = None
    service: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)


class RcaClusterItem(BaseModel):
    """A recurrence family: terminal incidents sharing a cause signature. size ≥ min_size
    nominates a PROBLEM candidate (promote endpoint links the members)."""
    signature: RcaClusterSignature = Field(default_factory=RcaClusterSignature)
    count: int = 0
    score: float = 0
    sev_worst: int = 4
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    has_open_problem: bool = False
    open_problem_id: Optional[UUID] = None
    open_problem_number: Optional[str] = None
    rca_hint: Optional[str] = None              # most common rca_category among members
    suggested_problem_title: Optional[str] = None
    ticket_ids: List[UUID] = Field(default_factory=list)
    tickets: List[RcaClusterTicket] = Field(default_factory=list)   # top-5 preview


class RcaClustersResponse(BaseModel):
    days: int = 90
    min_size: int = 3
    clusters: List[RcaClusterItem] = Field(default_factory=list)
    scanned: int = 0
    generated_at: Optional[datetime] = None


class RcaClusterPromote(BaseModel):
    """Promote a recurrence cluster into a PROBLEM record (investigating), linking the
    sealed subset of its tickets."""
    ticket_ids: List[UUID] = Field(min_length=2, max_length=50)
    title: str = Field(min_length=3, max_length=300)
    statement: Optional[str] = None             # problem description seed
    root_cause_hint: Optional[str] = None       # seeds problem.root_cause (draft language)


class RcaPromoteResult(BaseModel):
    ticket_id: UUID
    ok: bool
    reason: Optional[str] = None


class RcaClusterPromoteResponse(BaseModel):
    problem_id: UUID
    problem_number: Optional[str] = None
    linked: int = 0
    skipped: int = 0
    results: List[RcaPromoteResult] = Field(default_factory=list)


# ═══════════════════════ Command dashboard (composed) ═══════════════════════
# ONE payload for both incident dashboards (agent Fault Grid / admin Command Funnel).
# The seal scopes everything; the `admin` block is desk-wide and superuser-only.

class NextBreach(BaseModel):
    """Soonest live resolution deadline (not breached, not paused). minutes ≥ 0."""
    ticket_id: Optional[UUID] = None
    ticket_number: Optional[str] = None
    minutes: Optional[float] = None


class AgingBucket(BaseModel):
    """One rung of the ACTIVE aging ladder (age = now − created_at). sev12 = the
    SEV1∪SEV2 (major OR priority-critical) subset of the same bucket."""
    bucket: str                              # ">8h" | "4-8h" | "2-4h" | "1-2h" | "<1h"
    count: int = 0
    sev12: int = 0


class EscalationTiers(BaseModel):
    """ACTIVE escalation posture. l1/l2/l3 bucket escalation_level (≤0→l1, 1→l2, ≥2→l3)."""
    l1: int = 0
    l2: int = 0
    l3: int = 0
    escalated_total: int = 0                 # is_escalated == True
    auto_escalated_30d: int = 0              # auto_escalated_at within 30d


class IncidentQuality(BaseModel):
    """Outcome quality over incidents resolved in the last 30d."""
    csat_avg: Optional[float] = None
    csat_responses: int = 0
    reopen_rate_pct: Optional[float] = None
    fcr_pct: Optional[float] = None          # first-contact resolution (reopened_count 0/null)


class TasksLive(BaseModel):
    """Response-playbook progress over ACTIVE incidents (skipped tasks excluded)."""
    tickets_with_tasks: int = 0
    open: int = 0
    done: int = 0
    progress_pct: float = 0.0


class CommandDashboardExtras(BaseModel):
    """Always-returned enrichment (team-sealed for non-superusers)."""
    next_breach: Optional[NextBreach] = None
    aging_ladder: List[AgingBucket] = Field(default_factory=list)
    escalation: EscalationTiers = Field(default_factory=EscalationTiers)
    war_rooms: int = 0
    quality: IncidentQuality = Field(default_factory=IncidentQuality)
    tasks_live: TasksLive = Field(default_factory=TasksLive)


class LeaderRow(BaseModel):
    """Top solver: incidents resolved in 30d + live active load."""
    user_id: UUID
    name: Optional[str] = None
    resolved_30d: int = 0
    mttr_minutes: Optional[float] = None     # avg resolved − created
    active_load: int = 0                     # their current ACTIVE incidents as assignee


class TeamRow(BaseModel):
    """Per-team posture (ACTIVE) + 30d outcome metrics."""
    team_id: Optional[UUID] = None
    team_name: Optional[str] = None
    active: int = 0
    sla_met_pct: float = 0.0                 # (active − breached − at_risk) / active
    mttr_minutes: Optional[float] = None
    csat_avg: Optional[float] = None
    reopen_pct: Optional[float] = None


class RcaSummary(BaseModel):
    """RCA program rollup — pipeline + coverage from _rca_stats(30d), latency/kedb from analytics."""
    coverage_pct: Optional[float] = None
    owed: int = 0
    pending: int = 0
    returned: int = 0
    validated: int = 0
    stale: int = 0
    cycle_time_median_h: Optional[float] = None
    review_latency_median_h: Optional[float] = None
    kedb_known_errors: int = 0
    kedb_workarounds: int = 0


class PirSummary(BaseModel):
    """PIR program rollup — lifted verbatim from the PIR board's lockstep stats."""
    owed: int = 0
    draft: int = 0
    in_review: int = 0
    approved: int = 0
    published: int = 0
    actions_open: int = 0
    actions_overdue: int = 0
    actions_due: int = 0
    coverage_pct: Optional[float] = None
    median_review_hours_30d: Optional[float] = None
    published_30d: int = 0


class RecurringRow(BaseModel):
    """One recurrence cluster (from rca_clusters), flattened for the radar."""
    signature: str
    count: int = 0
    score: float = 0.0
    sev_worst: int = 4
    has_open_problem: bool = False
    suggested_problem_title: Optional[str] = None


class HeatCell(BaseModel):
    """Escalation heatmap cell — tier 1..3 × day_index 0..6 (0 = 6 days ago, 6 = today)."""
    tier: int
    day_index: int
    count: int = 0


class BusyCell(BaseModel):
    """Creation-density cell — weekday 0=Mon..6=Sun × hour 0..23 (nonzero cells only)."""
    weekday: int
    hour: int
    count: int = 0


class AdminIncidentBlock(BaseModel):
    """Superuser-only desk-wide intelligence (None for non-superusers)."""
    leaderboard: List[LeaderRow] = Field(default_factory=list)
    per_team: List[TeamRow] = Field(default_factory=list)
    rca: RcaSummary = Field(default_factory=RcaSummary)
    pir: PirSummary = Field(default_factory=PirSummary)
    recurring: List[RecurringRow] = Field(default_factory=list)
    escalation_heatmap: List[HeatCell] = Field(default_factory=list)
    busy_hours: List[BusyCell] = Field(default_factory=list)


class CommandDashboardResponse(BaseModel):
    generated_at: datetime
    is_superuser: bool = False
    agent: IncidentStatsResponse             # = incident_stats(db, admin) verbatim
    extras: CommandDashboardExtras = Field(default_factory=CommandDashboardExtras)
    admin: Optional[AdminIncidentBlock] = None   # None unless is_superuser
