"""Support Desk — Phase-3 workspace schemas: Team, Queue, Saved View, Template."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ─────────────────────────── Team ───────────────────────────
_ASSIGN_METHODS = {"manual", "round_robin", "load_balanced"}
_MEMBER_ROLES = {"lead", "agent", "collaborator"}
_REASSIGN_STRATEGIES = {"auto", "unassign", "reassign"}


def _check_assignment_method(v):
    if v is not None and v not in _ASSIGN_METHODS:
        raise ValueError(f"assignment_method must be one of {sorted(_ASSIGN_METHODS)}")
    return v


def _check_member_roles(v):
    for uid, role in (v or {}).items():
        if role not in _MEMBER_ROLES:
            raise ValueError(f"member_roles['{uid}'] must be one of {sorted(_MEMBER_ROLES)}")
    return v


class TeamCreate(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    lead_user_id: Optional[UUID] = None
    member_ids: List[UUID] = Field(default_factory=list)
    member_roles: Dict[str, str] = Field(default_factory=dict)   # {user_id: lead|agent|collaborator}
    # routing
    request_types: List[str] = Field(default_factory=list)       # ticket types this team handles
    category_ids: List[UUID] = Field(default_factory=list)       # categories this team owns
    auto_assign: bool = False
    assignment_method: str = "round_robin"                       # manual|round_robin|load_balanced
    # service profile
    business_hours: Dict[str, Any] = Field(default_factory=dict)
    default_sla_package_id: Optional[UUID] = None
    default_priority: Optional[str] = None

    _v_method = field_validator("assignment_method")(_check_assignment_method)
    _v_roles = field_validator("member_roles")(_check_member_roles)


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    lead_user_id: Optional[UUID] = None
    member_ids: Optional[List[UUID]] = None
    member_roles: Optional[Dict[str, str]] = None
    request_types: Optional[List[str]] = None
    category_ids: Optional[List[UUID]] = None
    auto_assign: Optional[bool] = None
    assignment_method: Optional[str] = None
    business_hours: Optional[Dict[str, Any]] = None
    default_sla_package_id: Optional[UUID] = None
    default_priority: Optional[str] = None
    is_active: Optional[bool] = None
    # Reassignment DIRECTIVE (not columns — popped by the router before setattr):
    # when the update removes members who still own active team tickets, the caller must
    # say what happens to that work: 'auto' (team assignment_method over the remaining
    # roster), 'unassign' (back to the team queue), or 'reassign' (+ reassign_to).
    reassign_strategy: Optional[str] = None
    reassign_to: Optional[UUID] = None

    _v_method = field_validator("assignment_method")(_check_assignment_method)
    _v_roles = field_validator("member_roles")(_check_member_roles)

    @field_validator("reassign_strategy")
    @classmethod
    def _v_strategy(cls, v):
        if v is not None and v not in _REASSIGN_STRATEGIES:
            raise ValueError(f"reassign_strategy must be one of {sorted(_REASSIGN_STRATEGIES)}")
        return v


class TeamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    lead_user_id: Optional[UUID] = None
    member_ids: List[Any] = Field(default_factory=list)
    member_roles: Dict[str, Any] = Field(default_factory=dict)
    request_types: List[Any] = Field(default_factory=list)
    category_ids: List[Any] = Field(default_factory=list)
    auto_assign: bool = False
    assignment_method: str = "round_robin"
    business_hours: Dict[str, Any] = Field(default_factory=dict)
    default_sla_package_id: Optional[UUID] = None
    default_priority: Optional[str] = None
    is_active: bool
    created_at: datetime
    # enriched
    lead_name: Optional[str] = None
    member_count: Optional[int] = None
    open_ticket_count: Optional[int] = None
    members: List[Dict[str, Any]] = Field(default_factory=list)   # [{id,name,role,is_agent,designation}]


# ───────────── Team Command (admin oversight desk) — overview + guards ─────────────
class MemberImpactEntry(BaseModel):
    user_id: UUID
    name: Optional[str] = None
    open_count: int = 0


class MemberImpactResponse(BaseModel):
    team_id: UUID
    total_open: int = 0
    members: List[MemberImpactEntry] = Field(default_factory=list)


class TeamFlowPoint(BaseModel):
    day: datetime
    inflow: int = 0
    outflow: int = 0


class TeamOverviewCard(BaseModel):
    id: UUID
    name: str
    code: Optional[str] = None
    color: Optional[str] = None
    is_active: bool = True
    auto_assign: bool = False
    assignment_method: str = "round_robin"
    business_hours: Dict[str, Any] = Field(default_factory=dict)
    request_types: List[str] = Field(default_factory=list)
    lead_user_id: Optional[UUID] = None
    lead_name: Optional[str] = None
    member_count: int = 0
    agent_count: int = 0                      # workable (non-collaborator) members
    coverage_open: Optional[bool] = None      # inside business hours right now? None = unknown
    # live counts (active = non-terminal, merged tombstones excluded)
    open: int = 0
    unassigned: int = 0
    breached: int = 0
    due_soon: int = 0
    idle: int = 0
    on_hold: int = 0
    critical: int = 0
    escalated_in: int = 0                     # active tickets escalated INTO this team
    # workload distribution over the workable roster (incl. zero-load members)
    load_min: Optional[int] = None
    load_max: Optional[int] = None
    load_avg: Optional[float] = None
    # speed & quality
    resolved_7d: int = 0
    mttr_p50_7d: Optional[float] = None       # minutes, pause-credited
    frt_p50_7d: Optional[float] = None        # minutes
    csat_30d: Optional[float] = None
    csat_n_30d: int = 0
    flow: List[TeamFlowPoint] = Field(default_factory=list)   # 7 daily buckets


class TeamsOverviewResponse(BaseModel):
    generated_at: datetime
    team_count: int = 0
    teams: List[TeamOverviewCard] = Field(default_factory=list)
    # fleet rollup for the hero console
    totals: Dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────── Queue ───────────────────────────
_SERVE_ORDERS = {"priority_age", "sla_breach"}


def _check_tier(v):
    if v is not None and v not in (1, 2, 3):
        raise ValueError("tier must be 1, 2 or 3 (or null for an untiered queue)")
    return v


def _check_serve_order(v):
    if v is not None and v not in _SERVE_ORDERS:
        raise ValueError(f"serve_order must be one of {sorted(_SERVE_ORDERS)}")
    return v


def _check_queue_priority(v):
    if v is not None and not (1 <= int(v) <= 100):
        raise ValueError("queue_priority must be between 1 and 100")
    return v


def _check_capacity(v):
    if v is not None and int(v) < 1:
        raise ValueError("capacity_limit must be at least 1 (leave it empty for unlimited)")
    return v


class QueueCreate(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    team_id: Optional[UUID] = None
    auto_assign: bool = False
    assignment_method: str = "round_robin"   # manual | round_robin | load_balanced
    category_ids: List[UUID] = Field(default_factory=list)
    # queue engine
    tier: Optional[int] = None               # 1|2|3 support tier
    skill_ids: List[UUID] = Field(default_factory=list)
    serve_order: str = "priority_age"        # priority_age | sla_breach
    queue_priority: int = 50                 # 1-100 cross-queue drain order
    max_agent_load: Optional[int] = None
    is_default: bool = False
    business_hours: Optional[Dict[str, Any]] = None
    # config v2
    sla_package_id: Optional[UUID] = None      # per-queue SLA policy (org > queue > default)
    capacity_limit: Optional[int] = None       # open-ticket cap; None = unlimited
    overflow_queue_id: Optional[UUID] = None   # spill target when at capacity

    _v_method = field_validator("assignment_method")(_check_assignment_method)
    _v_tier = field_validator("tier")(_check_tier)
    _v_serve = field_validator("serve_order")(_check_serve_order)
    _v_qpri = field_validator("queue_priority")(_check_queue_priority)
    _v_cap = field_validator("capacity_limit")(_check_capacity)


class QueueUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    team_id: Optional[UUID] = None
    auto_assign: Optional[bool] = None
    assignment_method: Optional[str] = None
    category_ids: Optional[List[UUID]] = None
    is_active: Optional[bool] = None
    # queue engine
    tier: Optional[int] = None
    skill_ids: Optional[List[UUID]] = None
    serve_order: Optional[str] = None
    queue_priority: Optional[int] = None
    max_agent_load: Optional[int] = None
    is_default: Optional[bool] = None
    business_hours: Optional[Dict[str, Any]] = None
    # config v2
    sla_package_id: Optional[UUID] = None
    capacity_limit: Optional[int] = None
    overflow_queue_id: Optional[UUID] = None
    # Delete/deactivate directive (not a column — popped by the router): where open
    # tickets go when this queue is removed.
    reassign_to: Optional[UUID] = None

    _v_method = field_validator("assignment_method")(_check_assignment_method)
    _v_tier = field_validator("tier")(_check_tier)
    _v_serve = field_validator("serve_order")(_check_serve_order)
    _v_qpri = field_validator("queue_priority")(_check_queue_priority)
    _v_cap = field_validator("capacity_limit")(_check_capacity)


class QueueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    team_id: Optional[UUID] = None
    auto_assign: bool
    assignment_method: str = "round_robin"
    category_ids: List[Any] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    # queue engine (all defaulted — stable against pre-migration rows)
    tier: Optional[int] = None
    skill_ids: List[Any] = Field(default_factory=list)
    serve_order: str = "priority_age"
    queue_priority: int = 50
    max_agent_load: Optional[int] = None
    is_default: bool = False
    business_hours: Optional[Dict[str, Any]] = None
    # config v2 (all defaulted — stable against pre-migration rows)
    sla_package_id: Optional[UUID] = None
    capacity_limit: Optional[int] = None
    overflow_queue_id: Optional[UUID] = None
    # enriched
    team_name: Optional[str] = None
    open_ticket_count: Optional[int] = None


# ───────────── Queue engine — overview / stats / tier board / skills ─────────────
class QueueFlowPoint(BaseModel):
    day: datetime
    inflow: int = 0
    outflow: int = 0


class QueueOverviewCard(BaseModel):
    id: UUID
    name: str
    code: Optional[str] = None
    color: Optional[str] = None
    tier: Optional[int] = None
    is_active: bool = True
    is_default: bool = False
    auto_assign: bool = False
    assignment_method: str = "round_robin"
    serve_order: str = "priority_age"
    queue_priority: int = 50
    team_id: Optional[UUID] = None
    team_name: Optional[str] = None
    category_count: int = 0
    skill_count: int = 0
    rule_count: int = 0                       # active routing rules targeting this queue
    agents_total: int = 0
    agents_online: int = 0
    coverage_open: Optional[bool] = None      # inside business hours right now? None = unknown
    capacity_limit: Optional[int] = None      # open-ticket cap (config v2); None = unlimited
    at_capacity: bool = False                 # open >= capacity_limit right now
    # live counts (active = non-terminal, merged tombstones excluded)
    open: int = 0
    in_progress: int = 0
    unassigned: int = 0
    breached: int = 0
    due_soon: int = 0
    critical: int = 0
    on_hold: int = 0
    # speed & quality
    avg_wait_mins: Optional[float] = None     # created → first assignment, 7d
    oldest_wait_mins: Optional[float] = None  # oldest still-unassigned ticket
    sla_attainment_7d: Optional[float] = None # % of 7d-resolved tickets inside target
    resolved_7d: int = 0
    health: str = "green"                     # green | amber | red
    flow: List[QueueFlowPoint] = Field(default_factory=list)  # 7 daily buckets
    # ── Vitals Bay telemetry (additive, 2026-07) ──
    aging: Dict[str, int] = Field(default_factory=dict)   # open-ticket age buckets: lt_1h/h1_4/h4_24/d1_3/gt_3d
    burn_rate_hr: Optional[float] = None      # resolved in trailing 4h ÷ 4
    drain_eta_mins: Optional[float] = None    # open ÷ burn rate (None when burn is 0 or nothing open)
    crew_capacity: Optional[int] = None       # agents_total × max_agent_load (None = uncapped crew)
    load_pct: Optional[float] = None          # open ÷ crew_capacity × 100
    reopens_range: int = 0                    # 'reopened' activities landing in this queue over the range


class TierFlowEdge(BaseModel):
    """Escalation flow between tiers over the range (the Sankey edges)."""
    from_tier: int
    to_tier: int
    count: int = 0


class QueuesOverviewResponse(BaseModel):
    generated_at: datetime
    queue_count: int = 0
    queues: List[QueueOverviewCard] = Field(default_factory=list)
    tier_rollup: Dict[str, Any] = Field(default_factory=dict)   # {"1": {...}, "2": {...}, "3": {...}, "untiered": {...}}
    tier_flow: List[TierFlowEdge] = Field(default_factory=list)
    totals: Dict[str, Any] = Field(default_factory=dict)        # fleet rollup for the hero
    auto_routed_today: int = 0
    skips_today: int = 0
    # ── Vitals Bay telemetry (additive, 2026-07 — every key optional so old clients keep working) ──
    flow_interval: str = "day"                                  # 'day' | 'hour' (hourly only when days <= 2)
    deltas: Dict[str, Any] = Field(default_factory=dict)        # {key: {now, prev, pct}} period-over-period
    aging: Dict[str, int] = Field(default_factory=dict)         # fleet open-ticket age histogram
    sla_split: Dict[str, Any] = Field(default_factory=dict)     # {response, resolution, by_priority:{...}}
    burn: Dict[str, Any] = Field(default_factory=dict)          # {burn_rate_hr, drain_eta_mins}
    utilization: Dict[str, Any] = Field(default_factory=dict)   # {load_pct, crew_capacity, open_capped, top_agents:[...]}
    breach_horizon: List[Dict[str, Any]] = Field(default_factory=list)  # next tickets about to breach
    reopens_range: int = 0                                      # fleet 'reopened' events in range


class QueueStatsResponse(BaseModel):
    """Queue drawer drill — one queue, deep."""
    id: UUID
    name: str
    generated_at: datetime
    card: QueueOverviewCard
    status_counts: Dict[str, int] = Field(default_factory=dict)
    priority_counts: Dict[str, int] = Field(default_factory=dict)
    load: List[Dict[str, Any]] = Field(default_factory=list)     # [{user_id,name,status,open_count}]
    categories: List[Dict[str, Any]] = Field(default_factory=list)
    skills: List[Dict[str, Any]] = Field(default_factory=list)
    rules: List[Dict[str, Any]] = Field(default_factory=list)    # rules routing INTO this queue
    recent_activity: List[Dict[str, Any]] = Field(default_factory=list)


class TierBoardResponse(BaseModel):
    """One tier's working queue — tickets + the stats block, in one request."""
    tier: int
    generated_at: datetime
    items: List[Any] = Field(default_factory=list)               # TicketResponse dicts (enriched)
    total: int = 0
    queues: List[Dict[str, Any]] = Field(default_factory=list)   # [{id,name,color,open}] tier queues for the filter rail
    stats: Dict[str, Any] = Field(default_factory=dict)          # status/priority counts, oldest_wait, my_load, skips_today, escalated_in/out


class ServeNextResponse(BaseModel):
    ticket: Optional[Any] = None                                 # enriched TicketResponse dict, or None when drained
    remaining: int = 0
    reason: Optional[str] = None                                 # why empty: 'drained' | 'all_viewed' | 'no_queues'


class SkipCreate(BaseModel):
    reason_code: str                                             # not_my_skill | need_info | duplicate_suspect | blocked | other
    note: Optional[str] = None


class TierEscalateRequest(BaseModel):
    to_tier: int
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    diagnosis: Optional[str] = None                              # required for L2→L3 (enforced in the router)
    queue_id: Optional[UUID] = None                              # explicit target queue override

    _v_tier = field_validator("to_tier")(_check_tier)


class TierDescendRequest(BaseModel):
    to_tier: int
    reason_code: Optional[str] = None                            # resolved_at_tier | misrouted | needs_basic_troubleshooting
    reason: Optional[str] = None
    queue_id: Optional[UUID] = None

    _v_tier = field_validator("to_tier")(_check_tier)


# ─────────────────────────── Skill ───────────────────────────
class SkillCreate(BaseModel):
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    agent_ids: List[UUID] = Field(default_factory=list)


class SkillUpdate(BaseModel):
    name: Optional[str] = None
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    agent_ids: Optional[List[UUID]] = None
    is_active: Optional[bool] = None


class SkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    code: Optional[str] = None
    description: Optional[str] = None
    color: Optional[str] = None
    agent_ids: List[Any] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    # enriched
    agents: List[Dict[str, Any]] = Field(default_factory=list)   # [{id,name}]
    queue_count: int = 0                                         # queues requiring this skill


class AgentStatusEntry(BaseModel):
    user_id: UUID
    name: Optional[str] = None
    status: str = "online"
    status_note: Optional[str] = None
    changed_at: Optional[datetime] = None
    open_count: int = 0
    team_ids: List[Any] = Field(default_factory=list)


class AgentStatusRosterResponse(BaseModel):
    generated_at: datetime
    me: Optional[AgentStatusEntry] = None
    agents: List[AgentStatusEntry] = Field(default_factory=list)


class MyStatusUpdate(BaseModel):
    status: str                                                  # online | away | focus | offline
    status_note: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _v_status(cls, v):
        if v not in {"online", "away", "focus", "offline"}:
            raise ValueError("status must be one of ['away', 'focus', 'offline', 'online']")
        return v


# ─────────────────────────── Routing rules — simulate ───────────────────────────
class RuleSimulateRequest(BaseModel):
    """A sample ticket payload to dry-run through the rule engine."""
    subject: Optional[str] = None
    description: Optional[str] = None
    ticket_type: Optional[str] = None
    priority: Optional[str] = None
    source: Optional[str] = None
    impact: Optional[str] = None
    urgency: Optional[str] = None
    category_id: Optional[UUID] = None
    subcategory_id: Optional[UUID] = None
    organization_id: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list)


class RuleSimulateResponse(BaseModel):
    matched: List[Dict[str, Any]] = Field(default_factory=list)  # [{rule_id,name,order_index,actions,stopped}]
    decision: Dict[str, Any] = Field(default_factory=dict)       # {queue_id,queue_name,team_id,team_name,priority,sla_package_id,tags,via}
    fallback_used: bool = False                                  # category/type router decided (no rule matched)


# ─────────────────────────── Saved View ───────────────────────────
class SavedViewCreate(BaseModel):
    name: str
    scope: str = "all"
    filters: Dict[str, Any] = Field(default_factory=dict)
    columns: List[str] = Field(default_factory=list)
    sort_by: Optional[str] = None
    sort_dir: Optional[str] = None
    is_shared: bool = False


class SavedViewUpdate(BaseModel):
    name: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    columns: Optional[List[str]] = None
    sort_by: Optional[str] = None
    sort_dir: Optional[str] = None
    is_shared: Optional[bool] = None


class SavedViewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    owner_user_id: UUID
    name: str
    scope: str
    filters: Dict[str, Any] = Field(default_factory=dict)
    columns: List[Any] = Field(default_factory=list)
    sort_by: Optional[str] = None
    sort_dir: Optional[str] = None
    is_shared: bool
    created_at: datetime


# ─────────────────────────── Template ───────────────────────────
class TemplateCreate(BaseModel):
    name: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    ticket_type: Optional[str] = None
    priority: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    checklist: List[Dict[str, Any]] = Field(default_factory=list)
    # Template Studio
    status: str = "active"                      # draft | active (archived only via update)
    default_sla_package_id: Optional[UUID] = None
    default_assignee_id: Optional[UUID] = None
    icon: Optional[str] = None                  # lucide key OR emoji
    accent: Optional[str] = None                # hex card identity
    pinned: bool = False
    sort_order: int = 0
    visibility: str = "global"                  # global | team | personal (agents are FORCED to 'personal')


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    ticket_type: Optional[str] = None
    priority: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[List[str]] = None
    checklist: Optional[List[Dict[str, Any]]] = None
    is_active: Optional[bool] = None            # legacy alias: True->active / False->archived (used only when status absent)
    # Template Studio
    status: Optional[str] = None                # draft | active | archived
    default_sla_package_id: Optional[UUID] = None
    default_assignee_id: Optional[UUID] = None
    icon: Optional[str] = None
    accent: Optional[str] = None
    pinned: Optional[bool] = None
    sort_order: Optional[int] = None
    visibility: Optional[str] = None            # superuser-only; stripped from agent payloads


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str] = None
    category_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    ticket_type: Optional[str] = None
    priority: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    tags: List[Any] = Field(default_factory=list)
    checklist: List[Any] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    # Template Studio (all defaulted — stable against pre-migration rows)
    status: str = "active"
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    version: int = 1
    pinned: bool = False
    sort_order: int = 0
    icon: Optional[str] = None
    accent: Optional[str] = None
    default_sla_package_id: Optional[UUID] = None
    default_assignee_id: Optional[UUID] = None
    updated_at: Optional[datetime] = None
    created_by_id: Optional[UUID] = None
    # Agent Template Desk (defaulted — stable against pre-migration rows)
    visibility: str = "global"                  # global | team | personal
    is_favorite: bool = False                   # per-CALLER star, stamped by the router


class TemplateDetailResponse(TemplateResponse):
    """Single-template view — adds the revision history (too heavy for the list)."""
    revisions: List[Dict[str, Any]] = Field(default_factory=list)


class TemplateApplyResponse(BaseModel):
    """Render-ready payload the New-Ticket intake prefills from (usage already counted)."""
    template_id: UUID
    name: str
    subject: Optional[str] = None
    body: Optional[str] = None
    ticket_type: Optional[str] = None
    priority: Optional[str] = None
    category_id: Optional[UUID] = None
    team_id: Optional[UUID] = None
    tags: List[Any] = Field(default_factory=list)
    checklist: List[Any] = Field(default_factory=list)
    default_sla_package_id: Optional[UUID] = None
    default_assignee_id: Optional[UUID] = None
    usage_count: int = 0
    version: int = 1


class TemplateRunRequest(BaseModel):
    """Run a template as a MACRO on an existing ticket (Zendesk macros).

    ``body`` arrives RENDERED — variable substitution stays client-side (the single
    engine in templateVariables.js, agent-reviewed in the run modal); the activity
    row still stamps template provenance so the record shows it was a template run.
    """
    mode: str = "internal_note"                 # internal_note | reply
    body: str
    apply_priority: bool = False                # adopt the template's priority
    merge_tags: bool = False                    # union the template's tags onto the ticket


class TemplateStatChip(BaseModel):
    id: UUID
    name: str
    icon: Optional[str] = None
    accent: Optional[str] = None
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    status: str = "active"


class TemplateCoverageEntry(BaseModel):
    id: Optional[UUID] = None
    name: str
    count: int


class TemplateStatsResponse(BaseModel):
    total: int
    active: int
    draft: int
    archived: int
    pinned: int
    unused: int
    usage_total: int
    tickets_from_templates: int
    tickets_from_templates_30d: int
    top_used: List[TemplateStatChip] = Field(default_factory=list)
    recently_used: List[TemplateStatChip] = Field(default_factory=list)
    coverage_by_category: List[TemplateCoverageEntry] = Field(default_factory=list)
    coverage_by_team: List[TemplateCoverageEntry] = Field(default_factory=list)
    # Per-CALLER analytics (agent Template Desk) — all defaulted so the admin
    # studio hero is byte-compatible with pre-desk responses.
    my_use_total: int = 0
    my_use_30d: int = 0
    my_top_used: List[TemplateStatChip] = Field(default_factory=list)
    my_recent: List[TemplateStatChip] = Field(default_factory=list)
