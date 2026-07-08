"""Support Desk — dashboard / KPI response schema."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, List
from uuid import UUID
from pydantic import BaseModel, Field

# The consolidated Pulse dashboard reuses the per-agent load + fastest-lap shapes the
# command-center already computes (SquadLoad / FastestLap). ticket.py is a leaf schema
# module (no import of this file) so this top-level import is cycle-safe.
from app.schemas.support_desk.ticket import SquadLoad, FastestLap


class SupportDashboardResponse(BaseModel):
    # KPI cards
    open_tickets: int = 0
    unassigned: int = 0
    pending: int = 0
    critical: int = 0
    overdue: int = 0
    sla_breached: int = 0
    resolved_today: int = 0
    closed_today: int = 0
    escalated: int = 0
    total_tickets: int = 0
    # Suspension Dock — deliberately parked tickets (SLA clock frozen)
    on_hold: int = 0
    # Averages / satisfaction
    avg_response_mins: Optional[float] = None
    avg_resolution_mins: Optional[float] = None
    csat: Optional[float] = None
    # Distributions (charts + the Liquid Triage Basin)
    priority_counts: Dict[str, int] = Field(default_factory=dict)
    status_counts: Dict[str, int] = Field(default_factory=dict)
    type_counts: Dict[str, int] = Field(default_factory=dict)


class SelfDashboardResponse(BaseModel):
    open: int = 0
    in_progress: int = 0
    pending: int = 0
    resolved: int = 0
    total: int = 0
    priority_counts: Dict[str, int] = Field(default_factory=dict)


# ───────────────────────── Pulse dashboard (the tickets landing page) ─────────────────────────
# One consolidated, team-sealed payload that powers the redesigned "Terminal" dashboard at
# /user/support/tickets/dashboard. `me` is every caller's personal requester view; `agent` is
# added only for support agents / superusers and carries the operational desk telemetry (sealed
# to the caller's teams exactly like command_center_stats — a superuser sees the whole desk).

class PulseFlowPoint(BaseModel):
    """One day on the 14-day inflow/outflow band (created vs resolved)."""
    day: datetime
    created: int = 0
    resolved: int = 0


class PulseAtRiskItem(BaseModel):
    """A ticket on the at-risk countdown rail — the next resolution deadlines (or already blown)."""
    id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    due_kind: str = "resolution"          # response | resolution
    due_at: Optional[datetime] = None
    minutes_left: Optional[int] = None    # negative once past due
    assigned_to_me: bool = False
    unassigned: bool = False
    breached: bool = False


class PulseAgentBlock(BaseModel):
    # ── personal workload (tickets ASSIGNED to me) ──
    my_open: int = 0
    my_in_progress: int = 0
    my_pending: int = 0
    my_on_hold: int = 0
    my_due_soon: int = 0
    my_breached: int = 0
    my_resolved_today: int = 0
    my_workload_score: int = 0
    # ── sealed desk situational tallies ──
    open_desk: int = 0
    unassigned: int = 0
    claimable: int = 0
    breached_active: int = 0
    due_soon: int = 0
    critical_active: int = 0
    escalated_active: int = 0
    status_counts: Dict[str, int] = Field(default_factory=dict)
    priority_counts: Dict[str, int] = Field(default_factory=dict)
    # ── SLA compliance (30-day surviving resolutions) ──
    sla_compliance_pct_30d: Optional[float] = None
    resolved_30d: int = 0
    # ── desk-wide speed (30-day means, minutes) ──
    mtta_minutes_30d: Optional[float] = None
    mttr_minutes_30d: Optional[float] = None
    # ── CSAT (30-day rated resolutions) ──
    csat_avg_30d: Optional[float] = None
    csat_count_30d: int = 0
    csat_response_rate_pct_30d: Optional[float] = None
    # ── 14-day flow (the terminal candles) ──
    flow: List[PulseFlowPoint] = Field(default_factory=list)
    created_14d: int = 0
    resolved_14d: int = 0
    backlog_delta_14d: int = 0            # created − resolved (positive = backlog growing)
    # ── aging depth ladder (open tickets, running age) ──
    aging: Dict[str, int] = Field(default_factory=dict)
    # ── at-risk deadlines (the countdown rail) ──
    at_risk: List[PulseAtRiskItem] = Field(default_factory=list)
    # ── reopen physics (30d) ──
    reopen_rate_30d: float = 0.0
    reopens_30d: int = 0
    # ── team roster + fastest lap ──
    roster: List[SquadLoad] = Field(default_factory=list)
    fastest_lap: Optional[FastestLap] = None
    team_count: int = 0
    team_names: List[str] = Field(default_factory=list)


class PulseDashboardResponse(BaseModel):
    me: SelfDashboardResponse
    is_agent: bool = False
    agent: Optional[PulseAgentBlock] = None
    generated_at: datetime


# ───────────────────────── Intel dashboard (ADMIN tickets dashboard) ─────────────────────────
# One consolidated, team-sealed payload powering the admin dashboard at
# /admin/support-desk/tickets/dashboard. Everything is range-parameterised (7/14/30/90 days)
# and sealed exactly like /support-desk/dashboard/ — superusers see the whole desk, agents
# only their teams. New surface: nothing here may be renamed once the frontend ships.

class IntelSummary(BaseModel):
    open_now: int = 0
    unassigned_now: int = 0
    on_hold_now: int = 0
    created_range: int = 0
    resolved_range: int = 0
    backlog_delta: int = 0                 # created − resolved (positive = backlog growing)
    mtta_minutes: Optional[float] = None   # mean ack time over range
    avg_first_response_minutes: Optional[float] = None
    mttr_minutes: Optional[float] = None
    first_response_met_pct: Optional[float] = None
    resolution_met_pct: Optional[float] = None


class IntelTrendPoint(BaseModel):
    """One day of desk flow — created vs resolved vs newly-breached."""
    day: datetime
    created: int = 0
    resolved: int = 0
    breached: int = 0


class IntelSlaPoint(BaseModel):
    """One day of SLA attainment — events bucketed by when they happened."""
    day: datetime
    responded: int = 0
    response_met: int = 0
    resolved: int = 0
    resolution_met: int = 0


class IntelTeamRow(BaseModel):
    """Per-team scoreboard row. team_id None = untriaged / unrouted work."""
    team_id: Optional[UUID] = None
    name: str = "Untriaged"
    color: Optional[str] = None
    open: int = 0
    unassigned: int = 0
    critical: int = 0
    breached_active: int = 0
    resolved_range: int = 0
    sla_met_pct: Optional[float] = None
    csat_avg: Optional[float] = None


class IntelAgentRow(BaseModel):
    """Desk-wide leaderboard row — resolution credit + live load."""
    agent_id: UUID
    name: Optional[str] = None
    resolved_range: int = 0
    mttr_minutes: Optional[float] = None
    csat_avg: Optional[float] = None
    csat_count: int = 0
    active_load: int = 0
    breaching: int = 0


class IntelQuality(BaseModel):
    reopen_rate_range: float = 0.0
    reopens_range: int = 0
    fcr_pct: Optional[float] = None        # resolved-in-range never reopened (one-touch proxy)


class IntelAtRiskItem(BaseModel):
    """Admin at-risk rail — next resolution deadlines desk-wide (or already blown)."""
    id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    team_name: Optional[str] = None
    assignee_name: Optional[str] = None
    due_at: Optional[datetime] = None
    minutes_left: Optional[int] = None     # negative once past due
    unassigned: bool = False
    breached: bool = False


class IntelPresence(BaseModel):
    """Live agent presence from the ticket-viewer heartbeat (rows fresher than ~60s)."""
    agents_online: int = 0
    tickets_watched: int = 0


class IntelIncidentItem(BaseModel):
    id: UUID
    ticket_number: Optional[str] = None
    subject: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    minutes_open: int = 0
    acknowledged: bool = False


class IntelHeatCell(BaseModel):
    """Busiest-hours matrix cell. dow follows Postgres extract(dow): 0 = Sunday."""
    dow: int
    hour: int
    count: int = 0


class IntelCsatPoint(BaseModel):
    day: datetime
    avg: Optional[float] = None
    count: int = 0


class IntelCsatBlock(BaseModel):
    avg: Optional[float] = None
    count: int = 0
    response_rate_pct: Optional[float] = None   # rated ÷ resolved_range
    distribution: Dict[str, int] = Field(default_factory=dict)  # "1".."5"
    trend: List[IntelCsatPoint] = Field(default_factory=list)


class SupportIntelResponse(BaseModel):
    generated_at: datetime
    range_days: int = 30
    is_superuser: bool = False
    summary: IntelSummary = Field(default_factory=IntelSummary)
    volume_trend: List[IntelTrendPoint] = Field(default_factory=list)
    sla_trend: List[IntelSlaPoint] = Field(default_factory=list)
    channel_mix: Dict[str, int] = Field(default_factory=dict)
    team_scoreboard: List[IntelTeamRow] = Field(default_factory=list)
    leaderboard: List[IntelAgentRow] = Field(default_factory=list)
    quality: IntelQuality = Field(default_factory=IntelQuality)
    aging: Dict[str, int] = Field(default_factory=dict)
    at_risk: List[IntelAtRiskItem] = Field(default_factory=list)
    presence: IntelPresence = Field(default_factory=IntelPresence)
    major_incidents_active: int = 0
    major_incidents: List[IntelIncidentItem] = Field(default_factory=list)
    busy_matrix: List[IntelHeatCell] = Field(default_factory=list)
    csat: IntelCsatBlock = Field(default_factory=IntelCsatBlock)
