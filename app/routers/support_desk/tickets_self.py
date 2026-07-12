"""Support Desk — employee self-service. prefix=/support-desk/me/tickets (auth=user).

Employees raise internal tickets, track status, reply, and rate resolution.
Registered BEFORE the broad admin tickets router (different prefix, but order-safe).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Response
from sqlalchemy import func, or_, and_, case, false, literal as sa_literal
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketComment, SdTicketActivity, SdTicketReminder
from app.models.support_desk.core import SdCategory
from app.models.support_desk.workspace import (
    SdTeam, SdTicketViewer, SdTicketTemplate, SdTemplateUsageEvent,
)
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, TicketType, TicketSource, CommentAuthorKind,
    ResolutionCode, RootCauseCategory,
    OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES, PRIORITY_ORDER, SLA_PAUSE_STATUSES,
    CHRONIC_REOPEN_THRESHOLD, ReopenSource, SUPPORT_RESOLVED_AUTOCLOSE_DAYS,
    ArchiveReason, SUPPORT_ARCHIVE_RETENTION_DAYS, SUPPORT_ARCHIVE_EXPIRING_SOON_DAYS,
    SUPPORT_CLOSED_AUTOARCHIVE_DAYS,
    HANDOFF_REASON_CODES, TEAM_IDLE_HOURS, TEAM_DUE_SOON_HOURS,
    EVT_TICKET_CREATED, EVT_TICKET_REPLIED, EVT_TICKET_STATUS, EVT_TICKET_ASSIGNED, EVT_TICKET_RESOLVED,
    EVT_TICKET_REOPENED,
)
from app.schemas.support_desk.ticket import (
    TicketCreate, TicketResponse, TicketDetailResponse, TicketListResponse,
    TicketCsat, CommentCreate, CommentResponse, TicketResolve,
    SelfTicketUpdate, SelfTicketWithdraw, SelfTicketReopen, SelfTicketAssign, MyCapabilities, TeamMember,
    CollaboratorChange, AssigneeOption,
)
from app.schemas.support_desk.core import CategoryResponse
from app.schemas.support_desk.workspace import TemplateRunRequest
# Visibility seal shared with the templates router — an agent can macro-run exactly
# the plates they can list (global ∪ own personal ∪ their teams'); no import cycle
# (templates.py imports models/schemas only).
from app.routers.support_desk.templates import _template_visible, _own_personal
from app.schemas.support_desk.dashboard import (
    SelfDashboardResponse, PulseDashboardResponse, PulseAgentBlock,
    PulseFlowPoint, PulseAtRiskItem,
)
from app.schemas.support_desk.ticket import (
    WorkbenchStats, CommandCenterStats, SquadLoad, FastestLap, CriticalStats,
    EscalationStats, BreachedStats, OverdueStats, OverdueWorst,
    ReopenedStats, ReopenedWorst,
    ResolvedStats, ResolutionTrendBucket, ResolverLoad,
    ClosedStats, ClosureTrendBucket, CloserLoad,
    ArchivedStats, ArchiveTrendBucket, ArchiverLoad,
    UnassignedQueueStats, UnassignedQueueTeam, ClaimNext, ClaimTicket,
    TeamQueueStats, TeamSwitcherEntry, TeamRosterEntry, TeamFlowBucket,
    TeamHotspot, TeamLeaderEntry, TicketHandoff,
    TeamDistributeRequest, TeamDistributeResult, TeamDistributeAssignment,
    CalendarEvent, CalendarDay, CalendarHoliday, CalendarBusiness, CalendarMeta,
    CalendarFeedResponse, ReminderCreate, ReminderUpdate, ReminderResponse,
)
from app.utils.dependencies import get_current_user
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.utils.support_desk.team_ops import team_ops_conds, team_on_shift
from app.utils.support_desk.workbench import compute_workbench
from app.utils.support_desk.assignment import (
    route_and_assign, match_route, teams_handling, _agents_of_team, _round_robin,
)
from app.utils.support_desk.rules import evaluate_rules, apply_default_queue
from app.routers.support_desk._common import (
    generate_ticket_number, resolve_sla_package, enrich_tickets, enrich_ticket, maybe_auto_close,
    _user_names, reactivate_on_customer_reply, auto_reopen_on_customer_reply,
    auto_resume_expired_holds, apply_overdue_scope, apply_reopen, apply_close_source,
    require_resolution_summary as _require_resolution_summary,
)

# HR reporting-manager model — a manager may assign tickets to their direct reports.
from app.models.hr.employee import Employee
from app.utils.hr.lifecycle_guard import SEPARATED

router = APIRouter(prefix="/support-desk/me/tickets", tags=["Support Desk — My Tickets"])

_PRIORITIES = {p.value for p in TicketPriority}
_TYPES = {t.value for t in TicketType}
_SOURCES = {s.value for s in TicketSource}


def _own(db: Session, ticket_id: UUID, user: User) -> SdTicket:
    t = db.query(SdTicket).filter(
        SdTicket.id == ticket_id, SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.raised_by_user_id == user.id,
    ).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    return t


def _is_agent(user: User) -> bool:
    return bool(getattr(user, "is_superuser", False) or getattr(user, "is_support_agent", False))


# Vendor involvement is INTERNAL — a plain requester must never learn which third party the
# desk handed their ticket to (matches ServiceNow/Zendesk: the customer only sees the top-level
# status). Blank every vendor field on a response destined for a non-worker viewer.
_VENDOR_INTERNAL_FIELDS = (
    "vendor_name", "vendor_ticket_ref", "vendor_status", "vendor_dispatched_at",
    "vendor_due_at", "vendor_reply_at", "vendor_reminder_count", "last_vendor_reminder_at",
    "vendor_wait_reason", "vendor_po_ref", "vendor_wait_ms", "vendor_overdue",
    "vendor_coordinator_name",
)


def _scrub_vendor_internals(resp):
    """Null the vendor internals on a TicketResponse/TicketDetailResponse in place, then return it."""
    for f in _VENDOR_INTERNAL_FIELDS:
        if hasattr(resp, f):
            setattr(resp, f, 0 if f == "vendor_reminder_count" else None)
    return resp


def _direct_report_user_ids(db: Session, user: User) -> set:
    """USER ids of active employees who currently report to this user (excludes
    separated). User ids — not Employee ids — so they line up with ticket
    assigned_agent_id / raised_by_user_id (both FK users.id)."""
    rows = db.query(Employee.user_id).filter(
        Employee.reporting_manager_id == user.id,
        Employee.is_deleted == False,  # noqa: E712
        or_(Employee.lifecycle_state.is_(None), Employee.lifecycle_state.notin_(SEPARATED)),
    ).all()
    return {r[0] for r in rows if r[0]}


def _as_uuid(v):
    try:
        return v if isinstance(v, UUID) else UUID(str(v))
    except Exception:
        return None


def _in_active_swarm(db: Session, ticket_id, user_id) -> bool:
    """True while a swarm is LIVE on the ticket and ``user_id`` is a participant. Swarm
    participation grants owner-tier act rights for the DURATION of the swarm only — join
    used to write the agent permanently into ``collaborators`` (owner-tier forever, long
    after the swarm ended). Now rights follow the swarm's life via this check; ending the
    swarm withdraws them. Cheap: one indexed lookup on the small swarm table."""
    from app.models.support_desk.collab import SdSwarmSession
    s = (db.query(SdSwarmSession)
         .filter(SdSwarmSession.ticket_id == ticket_id, SdSwarmSession.status == "active")
         .first())
    if not s:
        return False
    return str(user_id) in {str(u) for u in (s.participant_ids or [])}


def _is_lead(team, user_id) -> bool:
    """Does ``user_id`` lead ``team``? True for the ``lead_user_id`` column OR a
    ``member_roles`` entry of ``'lead'``. Two lead definitions coexist on SdTeam and
    could drift — a team saved with member_roles[uid]='lead' but a NULL lead_user_id
    left NOBODY recognized as lead by the actor gates (real defect: Tier 2 lead). Every
    authorization path resolves lead through this helper so the two can't diverge; team
    writes additionally normalize lead_user_id from member_roles (see workspace.py)."""
    uid = str(user_id)
    if team.lead_user_id and str(team.lead_user_id) == uid:
        return True
    return (team.member_roles or {}).get(uid) == "lead"


def _team_context(db: Session, user: User) -> dict:
    """Everything the user-panel 'team' surfaces need: the user's reporting reports,
    the support teams they belong to, and the derived assignee pool + purview.
      • scope_ids   — whose tickets the user may SEE on the team board
      • led_member_ids — whom the user may ASSIGN to (members of teams they lead)
      • team_ids    — support teams whose ticket queue the user may see
    """
    reports = _direct_report_user_ids(db, user)               # set[UUID]
    teams = db.query(SdTeam).filter(SdTeam.is_deleted == False, SdTeam.is_active == True).all()  # noqa: E712
    uid = str(user.id)
    mine, member_ids, led_member_ids, team_ids = [], set(), set(), []
    for t in teams:
        is_member = uid in [str(m) for m in (t.member_ids or [])]
        is_lead = _is_lead(t, user.id)
        if not (is_member or is_lead):
            continue
        mine.append(t)
        team_ids.append(t.id)
        mids = {x for x in (_as_uuid(m) for m in (t.member_ids or [])) if x}
        if t.lead_user_id:
            lu = _as_uuid(t.lead_user_id)
            if lu:
                mids.add(lu)
        member_ids |= mids
        if is_lead:
            led_member_ids |= mids
    scope_ids = set(reports) | member_ids | {user.id}
    return {"reports": reports, "teams": mine, "team_ids": team_ids,
            "member_ids": member_ids, "led_member_ids": led_member_ids, "scope_ids": scope_ids}


def _dispatch_safe(db: Session, event: str, recipient_user_id, ticket: SdTicket, *,
                   title: str, action_url: str):
    """Best-effort notification (mirrors tickets.py:dispatch_safe) — never breaks the txn."""
    if not recipient_user_id:
        return
    try:
        from app.utils.hr.notify import dispatch
        from app.utils.support_desk import wires
        wires.post_webhook(db, event, ticket, title)
        if not wires.allows(db, event):
            return
        deep = f"{action_url}{'&' if '?' in action_url else '?'}ticket={ticket.id}"
        dispatch(db, event, recipient_user_id,
                 context={"title": title, "message": f"{ticket.ticket_number}: {ticket.subject}",
                          "action_url": deep},
                 audience="SUPPORT")
    except Exception:
        pass


_RESOLUTION_CODES = {c.value for c in ResolutionCode}
_ROOT_CAUSES = {c.value for c in RootCauseCategory}


def _team_members_of(db: Session, team_id) -> set:
    """The member user-ids (+ lead) of one team — the precise reassignment pool when a
    ticket belongs to a team the caller is on."""
    if not team_id:
        return set()
    team = db.query(SdTeam).filter(SdTeam.id == team_id, SdTeam.is_deleted == False).first()  # noqa: E712
    if not team:
        return set()
    out = {x for x in (_as_uuid(m) for m in (team.member_ids or [])) if x}
    lead = _as_uuid(team.lead_user_id) if team.lead_user_id else None
    if lead:
        out.add(lead)
    return out


def _my_handled_taxonomy(ctx: dict) -> tuple[set, set]:
    """The request-types + category-ids the user's own teams declare — drives the
    'triage pool' (untriaged tickets that route to one of my teams)."""
    types, cats = set(), set()
    for tm in ctx.get("teams", []):
        for rt in (tm.request_types or []):
            if rt:
                types.add(str(rt))
        for cid in (tm.category_ids or []):
            cu = _as_uuid(cid)
            if cu:
                cats.add(cu)
    return types, cats


def _command_center_filter(user: User, ctx: dict):
    """The un-bypassable team-scope condition for the All-Tickets command center:
    tickets assigned to me, tickets I collaborate on, tickets on one of MY teams,
    OR the UNOWNED untriaged triage pool routing to my teams. Reused to hard-seal the
    agent list + workbench so a non-superuser can never enumerate another team's desk.

    Deliberately EXCLUDED (leak fixes):
      • raised-by-me — the requester hat lives on /me/tickets (list, dashboard, drawer
        self-view fallback). A ticket I raised that another team works must NOT surface
        on my agent desk looking like their ticket leaked to me.
      • triage pool tickets that already have an assignee — once claimed by an agent of
        another team (team_id still NULL), it is THEIR ticket, not my pool."""
    uid = user.id
    conds = [
        SdTicket.assigned_agent_id == uid,
        SdTicket.collaborators.op('@>')(func.jsonb_build_array(str(uid))),
    ]
    if ctx["team_ids"]:
        conds.append(SdTicket.team_id.in_(ctx["team_ids"]))
    handled_types, handled_cats = _my_handled_taxonomy(ctx)
    tc = []
    if handled_types:
        tc.append(SdTicket.ticket_type.in_(handled_types))
    if handled_cats:
        tc.append(SdTicket.category_id.in_(handled_cats))
    if tc:
        conds.append(and_(SdTicket.team_id.is_(None),
                          SdTicket.assigned_agent_id.is_(None), or_(*tc)))
    return or_(*conds)


def _team_queue_filter(ctx: dict):
    """Strict 'my team's claimable pool' — tickets routed to a team I'm on, OR the
    untriaged triage pool (team_id NULL) whose type/category routes to one of my teams.
    The precise variant of _command_center_filter WITHOUT the raised-by-me / collaborator
    branches, so the Unassigned queue is exactly my teams' work — never a ticket I merely
    raised that routes elsewhere. Non-superusers only (superusers get the whole desk)."""
    conds = []
    if ctx["team_ids"]:
        conds.append(SdTicket.team_id.in_(ctx["team_ids"]))
    handled_types, handled_cats = _my_handled_taxonomy(ctx)
    tc = []
    if handled_types:
        tc.append(SdTicket.ticket_type.in_(handled_types))
    if handled_cats:
        tc.append(SdTicket.category_id.in_(handled_cats))
    if tc:
        conds.append(and_(SdTicket.team_id.is_(None), or_(*tc)))
    if not conds:
        return false()   # on no teams + no handled taxonomy ⇒ an empty claimable pool
    return or_(*conds)


def _claim_eligible(t: SdTicket, ctx: dict, is_su: bool) -> bool:
    """May the caller CLAIM this specific unowned ticket? The per-row mirror of
    _team_queue_filter: superuser (whole desk) OR the ticket is routed to a team they're
    on OR it is untriaged and its request type / category routes to one of their teams.
    Same logic the queue lists by, so what an agent sees == what they can claim (no leak)."""
    if is_su:
        return True
    if t.team_id is not None:
        return t.team_id in ctx["team_ids"]
    handled_types, handled_cats = _my_handled_taxonomy(ctx)
    return bool((t.ticket_type and t.ticket_type in handled_types)
                or (t.category_id and t.category_id in handled_cats))


def _stamp_team_on_claim(t: SdTicket, ctx: dict, target_id) -> None:
    """Claim-from-triage: an untriaged ticket (team_id NULL) leaves the triage pool the
    moment it's claimed — stamp the caller's team that handles its type/category (else a
    team the assignee is on, else the caller's first team) so it enters the proper team
    queue. Shared by manager_assign_ticket + claim_next (single source of truth)."""
    if t.team_id is not None or not ctx["teams"]:
        return
    chosen = None
    for tm in ctx["teams"]:
        rts = [str(x) for x in (tm.request_types or [])]
        cats = [str(x) for x in (tm.category_ids or [])]
        if (t.ticket_type and t.ticket_type in rts) or (t.category_id and str(t.category_id) in cats):
            chosen = tm
            break
    if not chosen:
        tgt = str(target_id)
        for tm in ctx["teams"]:
            members = {str(m) for m in (tm.member_ids or [])} | ({str(tm.lead_user_id)} if tm.lead_user_id else set())
            if tgt in members:
                chosen = tm
                break
    t.team_id = (chosen or ctx["teams"][0]).id


def _can_work(db: Session, t: SdTicket, user: User, ctx: dict | None = None) -> bool:
    """May this user WORK the ticket (resolve / assign / collaborate / internal notes)?
    True when they are: a superuser, the assignee, a named collaborator, a member of the
    ticket's owning team, on a team that handles its (untriaged) type/category, or the
    reporting manager of the requester/assignee.

    NOTE: `is_support_agent` alone deliberately does NOT short-circuit to True anymore —
    that flag used to unlock EVERY ticket by id, bypassing the team seal the list surface
    enforces (an agent could resolve / read internal notes on another team's tickets).
    ServiceNow/Zendesk scope agents to their groups; we now do the same."""
    if getattr(user, "is_superuser", False):
        return True
    uid = str(user.id)
    if t.assigned_agent_id and str(t.assigned_agent_id) == uid:
        return True
    if uid in [str(c) for c in (t.collaborators or [])]:
        return True
    ctx = ctx or _team_context(db, user)
    if t.team_id and t.team_id in ctx["team_ids"]:
        return True
    # Untriaged pool routed to one of my teams — claimable, therefore workable.
    if t.team_id is None and _claim_eligible(t, ctx, False):
        return True
    reports = ctx["reports"]
    if (t.raised_by_user_id in reports) or (t.assigned_agent_id in reports):
        return True
    return False


def _require_self_actor(db: Session, t: SdTicket, user: User, action: str = "act on it",
                        ctx: dict | None = None) -> None:
    """OWNER-tier gate, self-router flavour (mirrors tickets._require_ticket_actor):
    commanding an ASSIGNED ticket's workflow (resolve, reassign, hand off, collaborators)
    is reserved to the assignee, their collaborators, the owning team's LEAD, the
    assignee's reporting manager, or a superuser. An UNASSIGNED ticket is team triage —
    anyone claim-eligible may act. A plain teammate gets a 403 with the sanctioned paths
    (claim / handoff / ask the lead) implied by the message."""
    if getattr(user, "is_superuser", False):
        return
    uid = str(user.id)
    if t.assigned_agent_id and str(t.assigned_agent_id) == uid:
        return
    if uid in [str(c) for c in (t.collaborators or [])]:
        return
    ctx = ctx or _team_context(db, user)
    if t.team_id and any(tm.id == t.team_id and _is_lead(tm, user.id) for tm in ctx["teams"]):
        return
    if not t.assigned_agent_id and _claim_eligible(t, ctx, False):
        return
    if (t.assigned_agent_id in ctx["reports"]) or (
            not t.assigned_agent_id and t.raised_by_user_id in ctx["reports"]):
        return
    # Live-swarm participants may act for the duration of the swarm (see _in_active_swarm).
    if _in_active_swarm(db, t.id, user.id):
        return
    raise HTTPException(
        403, f"This ticket is assigned to another agent — only they, the team lead, or an admin can {action}.")


def _route_authz(db: Session, t: SdTicket, user: User, target_id, verb: str = "route") -> dict:
    """Shared authorization for /assign + /handoff. Enforces, in order:
      1. REACH — the caller can route at all (superuser / manager with reports / team
         lead / member of the ticket's owning team / claim-eligible on untriaged).
      2. PURVIEW — the ticket is within the caller's world (their team's queue, their
         untriaged pool, or raised-by / assigned-to someone in their scope). The old
         `_is_agent` bypass here let ANY support agent re-route ANY team's ticket by id.
      3. OWNER DISCIPLINE — an ASSIGNED ticket is re-routed only by its assignee, a
         collaborator, the owning team's lead, the assignee's manager, or a superuser
         (ServiceNow/Zendesk reserve reassignment to owner/lead — no peer poaching;
         teammates claim unassigned work or receive a handoff instead).
      4. TARGET POOL — non-superusers route only to themselves, direct reports, members
         of teams they lead, or members of the ticket's owning team.
    Returns the caller's team context for follow-up bookkeeping."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    reports, led, scope, team_ids = ctx["reports"], ctx["led_member_ids"], ctx["scope_ids"], ctx["team_ids"]
    on_ticket_team = bool(t.team_id) and t.team_id in team_ids
    untriaged_mine = t.team_id is None and _claim_eligible(t, ctx, False)
    can_route = is_su or bool(reports) or bool(led) or on_ticket_team or untriaged_mine
    if not can_route:
        raise HTTPException(403, f"Only a reporting manager, team lead, or a member of the ticket's team can {verb} it.")
    in_purview = (is_su or on_ticket_team or untriaged_mine
                  or (t.raised_by_user_id in scope) or (t.assigned_agent_id in scope))
    if not in_purview:
        raise HTTPException(403, "This ticket is not within your team.")
    _require_self_actor(db, t, user, f"{verb} it", ctx)
    if not is_su:
        allowed = reports | led | {user.id} | (_team_members_of(db, t.team_id) if on_ticket_team else set())
        if t.team_id is None:
            allowed |= ctx["member_ids"]
        if target_id not in allowed:
            raise HTTPException(403, "You can only assign to yourself, your direct reports, or a member of the ticket's team.")
    return ctx


def _load_visible(db: Session, ticket_id: UUID, user: User, *, require_work: bool = False):
    """Load a ticket the user may at least SEE (requester or worker). Returns
    (ticket, can_work, is_requester). 404 if not visible; 403 if work is required but
    the user is only the requester."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket not found")
    is_requester = bool(t.raised_by_user_id) and str(t.raised_by_user_id) == str(user.id)
    can_work = _can_work(db, t, user)
    if not (is_requester or can_work):
        raise HTTPException(404, "Ticket not found")
    if require_work and not can_work:
        raise HTTPException(403, "You don't have permission to work this ticket.")
    return t, can_work, is_requester


def _self_counts(db: Session, user: User) -> SelfDashboardResponse:
    """The requester's own ticket counts — shared by /dashboard and /dashboard/pulse so the
    personal view is computed exactly one way."""
    base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False, SdTicket.raised_by_user_id == user.id)  # noqa: E712
    out = SelfDashboardResponse()
    out.total = base.count()
    out.open = base.filter(SdTicket.status == TicketStatus.OPEN.value).count()
    out.in_progress = base.filter(SdTicket.status == TicketStatus.IN_PROGRESS.value).count()
    out.pending = base.filter(SdTicket.status.in_([
        TicketStatus.PENDING_CUSTOMER.value, TicketStatus.PENDING_VENDOR.value])).count()
    out.resolved = base.filter(SdTicket.status.in_([
        TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value])).count()
    pr_rows = (base.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES))
               .with_entities(SdTicket.priority, func.count(SdTicket.id))
               .group_by(SdTicket.priority).all())
    out.priority_counts = {p.value: 0 for p in TicketPriority}
    for k, v in pr_rows:
        out.priority_counts[k] = v
    return out


@router.get("/dashboard", response_model=SelfDashboardResponse)
def my_dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _self_counts(db, user)


@router.get("/dashboard/pulse", response_model=PulseDashboardResponse)
def my_dashboard_pulse(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Consolidated 'Pulse' dashboard powering the tickets landing page (/user/support/tickets/dashboard).

    Every caller gets `me` (their personal requester counts). Support agents + superusers
    additionally get a team-sealed `agent` block: personal workload, sealed-desk distributions,
    SLA compliance %, desk-wide MTTA/MTTR, CSAT, a 14-day created-vs-resolved flow band, aging
    depth, the next at-risk SLA deadlines, the claimable pool, reopen rate, and the team roster.
    One call replaces the 4–6 per-desk stats fetches the page would otherwise fan out.

    The seal is identical to command_center_stats: superusers see the whole desk; agents are
    sealed to their teams via _command_center_filter. Literal path — declared before /{ticket_id}."""
    me = _self_counts(db, user)
    is_su = bool(getattr(user, "is_superuser", False))
    is_agent = _is_agent(user)
    out = PulseDashboardResponse(me=me, is_agent=is_agent, generated_at=sla_util.now_utc())
    if not is_agent:
        return out

    ctx = _team_context(db, user)
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d14 = now - timedelta(days=14)
    d30 = now - timedelta(days=30)
    open_set = OPEN_TICKET_STATUSES
    terminal = list(TERMINAL_TICKET_STATUSES)

    # Sealed desk base — superuser = whole desk, agent = their teams (single source of truth).
    desk = db.query(SdTicket).filter(SdTicket.is_deleted == False)  # noqa: E712
    if not is_su:
        desk = desk.filter(_command_center_filter(user, ctx))
    active_desk = desk.filter(SdTicket.status.in_(open_set))

    blk = PulseAgentBlock()

    # ── personal workload (tickets assigned to me) — reuse the heuristic workbench ──
    mine_base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False, SdTicket.assigned_agent_id == user.id)  # noqa: E712
    wb = compute_workbench(db, mine_base, actor=user)
    blk.my_open = wb.open
    blk.my_in_progress = wb.in_progress
    blk.my_pending = wb.pending_total
    blk.my_on_hold = wb.on_hold
    blk.my_due_soon = wb.sla_risk
    blk.my_breached = wb.sla_breached
    blk.my_resolved_today = wb.resolved_today
    blk.my_workload_score = wb.workload_score

    # ── sealed desk situational tallies + distributions ──
    blk.open_desk = active_desk.count()
    blk.unassigned = active_desk.filter(SdTicket.assigned_agent_id.is_(None)).count()
    blk.critical_active = active_desk.filter(SdTicket.priority == TicketPriority.CRITICAL.value).count()
    blk.escalated_active = active_desk.filter(SdTicket.is_escalated == True).count()  # noqa: E712
    blk.breached_active = active_desk.filter(or_(
        SdTicket.sla_response_breached == True,  # noqa: E712
        SdTicket.sla_resolution_breached == True)).count()  # noqa: E712
    blk.due_soon = active_desk.filter(
        SdTicket.sla_paused_since.is_(None),
        SdTicket.sla_resolution_breached == False,  # noqa: E712
        SdTicket.resolution_due_at.isnot(None),
        SdTicket.resolution_due_at > now,
        SdTicket.resolution_due_at <= now + timedelta(hours=2)).count()
    blk.claimable = _unassigned_pool(db, user, ctx, is_su).count()
    blk.status_counts = {(s or "unset"): int(c or 0) for s, c in
                         desk.with_entities(SdTicket.status, func.count(SdTicket.id))
                         .group_by(SdTicket.status).all()}
    blk.priority_counts = {p.value: 0 for p in TicketPriority}
    for p, c in (active_desk.with_entities(SdTicket.priority, func.count(SdTicket.id))
                 .group_by(SdTicket.priority).all()):
        if p in blk.priority_counts:
            blk.priority_counts[p] = int(c or 0)

    # ── SLA compliance over the 30-day surviving resolution record (merged excluded) ──
    res30 = desk.filter(SdTicket.merged_into_id.is_(None),
                        SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30)
    row = res30.with_entities(
        func.count(SdTicket.id),
        func.sum(case((SdTicket.sla_resolution_breached == False, 1), else_=0))).first()  # noqa: E712
    blk.resolved_30d = int(row[0] or 0)
    sla_met = int(row[1] or 0)
    blk.sla_compliance_pct_30d = (round(100.0 * sla_met / blk.resolved_30d, 1)
                                  if blk.resolved_30d else None)

    # ── desk-wide MTTA / MTTR (30-day means, minutes) ──
    mtta = (desk.filter(SdTicket.acknowledged_at.isnot(None), SdTicket.acknowledged_at >= d30)
            .with_entities(func.avg(func.extract("epoch", SdTicket.acknowledged_at - SdTicket.created_at)))
            .scalar())
    blk.mtta_minutes_30d = round(float(mtta) / 60.0, 1) if mtta is not None else None
    mttr = (desk.filter(SdTicket.merged_into_id.is_(None),
                        SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30)
            .with_entities(func.avg(func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)))
            .scalar())
    blk.mttr_minutes_30d = round(float(mttr) / 60.0, 1) if mttr is not None else None

    # ── CSAT over the 30-day rated resolutions ──
    rated = res30.filter(SdTicket.csat_score.isnot(None))
    crow = rated.with_entities(func.avg(SdTicket.csat_score), func.count(SdTicket.id)).first()
    if crow and crow[1]:
        blk.csat_avg_30d = round(float(crow[0]), 2)
        blk.csat_count_30d = int(crow[1] or 0)
    blk.csat_response_rate_pct_30d = (round(100.0 * blk.csat_count_30d / blk.resolved_30d, 1)
                                      if blk.resolved_30d else None)

    # ── 14-day flow: created vs resolved per day (date_trunc, zero-filled tz-aware) ──
    cday = func.date_trunc("day", SdTicket.created_at)
    cre_rows = {k: int(v or 0) for k, v in
                desk.filter(SdTicket.created_at >= d14)
                .with_entities(cday, func.count(SdTicket.id)).group_by(cday).all()}
    rday = func.date_trunc("day", SdTicket.resolved_at)
    res_rows = {k: int(v or 0) for k, v in
                desk.filter(SdTicket.merged_into_id.is_(None),
                            SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d14)
                .with_entities(rday, func.count(SdTicket.id)).group_by(rday).all()}

    def _on(day, rows):
        return next((v for k, v in rows.items() if k is not None and k.date() == day.date()), 0)

    blk.flow = [PulseFlowPoint(day=day, created=_on(day, cre_rows), resolved=_on(day, res_rows))
                for day in (sod - timedelta(days=i) for i in range(13, -1, -1))]
    blk.created_14d = sum(p.created for p in blk.flow)
    blk.resolved_14d = sum(p.resolved for p in blk.flow)
    blk.backlog_delta_14d = blk.created_14d - blk.resolved_14d

    # ── aging depth ladder over the open desk (running age from created_at) ──
    age_case = case(
        (SdTicket.created_at >= now - timedelta(hours=4), "<4h"),
        (SdTicket.created_at >= now - timedelta(hours=24), "4-24h"),
        (SdTicket.created_at >= now - timedelta(days=3), "1-3d"),
        (SdTicket.created_at >= now - timedelta(days=7), "3-7d"),
        else_=">7d")
    aged = {k: int(c or 0) for k, c in
            active_desk.with_entities(age_case, func.count(SdTicket.id)).group_by(age_case).all()}
    blk.aging = {b: aged.get(b, 0) for b in ("<4h", "4-24h", "1-3d", "3-7d", ">7d")}

    # ── at-risk countdown rail — nearest resolution deadlines still running (or blown) ──
    uid = user.id
    for t in (desk.filter(SdTicket.status.notin_(terminal),
                          SdTicket.sla_paused_since.is_(None),
                          SdTicket.resolution_due_at.isnot(None))
              .order_by(SdTicket.resolution_due_at.asc()).limit(8).all()):
        due = sla_util._aware(t.resolution_due_at)
        mins = int((due - now).total_seconds() // 60) if due else None
        blk.at_risk.append(PulseAtRiskItem(
            id=t.id, ticket_number=t.ticket_number, subject=t.subject,
            priority=t.priority, status=t.status, due_kind="resolution", due_at=due,
            minutes_left=mins, assigned_to_me=(t.assigned_agent_id == uid),
            unassigned=(t.assigned_agent_id is None),
            breached=bool(t.sla_resolution_breached)))

    # ── reopen physics (30d) — same denominator as the Resolved/Reopened desks ──
    blk.reopens_30d = desk.filter(SdTicket.merged_into_id.is_(None),
                                  SdTicket.last_reopened_at.isnot(None),
                                  SdTicket.last_reopened_at >= d30).count()
    solves = blk.resolved_30d + blk.reopens_30d
    blk.reopen_rate_30d = round(100.0 * blk.reopens_30d / solves, 1) if solves else 0.0

    # ── team roster + fastest lap (command-center idiom) ──
    sq = (active_desk.filter(SdTicket.assigned_agent_id.isnot(None))
          .with_entities(
              SdTicket.assigned_agent_id, func.count(SdTicket.id),
              func.sum(case((or_(SdTicket.sla_response_breached == True,  # noqa: E712
                                 SdTicket.sla_resolution_breached == True), 1), else_=0)),
              func.sum(case((SdTicket.priority == TicketPriority.CRITICAL.value, 1), else_=0))))
    if is_su:
        rows = sq.group_by(SdTicket.assigned_agent_id).order_by(func.count(SdTicket.id).desc()).limit(12).all()
    elif ctx["member_ids"]:
        rows = (sq.filter(SdTicket.assigned_agent_id.in_(list(ctx["member_ids"])))
                .group_by(SdTicket.assigned_agent_id).order_by(func.count(SdTicket.id).desc()).limit(20).all())
    else:
        rows = []
    fl = (desk.filter(SdTicket.merged_into_id.is_(None),
                      SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= sod,
                      SdTicket.assigned_agent_id.isnot(None))
          .with_entities(SdTicket.assigned_agent_id, func.count(SdTicket.id))
          .group_by(SdTicket.assigned_agent_id).order_by(func.count(SdTicket.id).desc()).first())
    name_ids = [r[0] for r in rows] + ([fl[0]] if fl else [])
    names = _user_names(db, name_ids) if name_ids else {}
    roster = [SquadLoad(agent_id=aid, name=names.get(str(aid)),
                        open=int(o or 0), breaching=int(b or 0), critical=int(c or 0))
              for aid, o, b, c in rows]
    roster.sort(key=lambda s: (-s.open, -s.breaching))
    blk.roster = roster
    if fl:
        blk.fastest_lap = FastestLap(agent_id=fl[0], name=names.get(str(fl[0])), count=int(fl[1] or 0))
    blk.team_count = len(ctx["teams"])
    blk.team_names = [tm.name for tm in ctx["teams"]]

    out.agent = blk
    return out


@router.get("/workbench", response_model=WorkbenchStats)
def my_workbench(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Workbench KPIs + insights powering the employee-panel My Tickets page.
    Agents: ASSIGNED-to-me only — the agent My list shows the assigned queue, so the
    stats/insights must reconcile with it (computing over raised∪assigned made the
    smart insights nudge merges on tickets assigned to OTHER agents). Plain employees:
    raised-or-assigned involvement, matching /me/tickets. Declared before /{ticket_id}."""
    if _is_agent(user):
        base = db.query(SdTicket).filter(
            SdTicket.is_deleted == False,  # noqa: E712
            SdTicket.assigned_agent_id == user.id,
        )
    else:
        base = db.query(SdTicket).filter(
            SdTicket.is_deleted == False,  # noqa: E712
            or_(SdTicket.raised_by_user_id == user.id, SdTicket.assigned_agent_id == user.id),
        )
    return compute_workbench(db, base, actor=user)


@router.get("/capabilities", response_model=MyCapabilities)
def my_capabilities(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Role-adaptive UI driver: am I an agent (full controls), a reporting manager
    (may assign to my team), and/or an admin? Also ships my support-team membership +
    the teams I LEAD so the drawer can gate owner-tier actions (escalate / move /
    resolve / reassign) to assignee-or-lead without an extra roundtrip.
    Declared before /{ticket_id}."""
    ctx = _team_context(db, user)
    uid = str(user.id)
    return MyCapabilities(
        is_admin=bool(getattr(user, "is_superuser", False)),
        is_agent=_is_agent(user),
        is_manager=len(ctx["reports"]) > 0,
        team_size=len(ctx["reports"]),
        member_team_ids=list(ctx["team_ids"]),
        lead_team_ids=[tm.id for tm in ctx["teams"] if _is_lead(tm, user.id)],
    )


@router.get("/team", response_model=list[TeamMember])
def my_team(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """My direct reports (as users) — the assignee pool a reporting manager may
    route tickets to. Ids are users.id so they match assigned_agent_id."""
    ids = _direct_report_user_ids(db, user)
    if not ids:
        return []
    rows = db.query(User).filter(User.id.in_(ids)).all()
    return [TeamMember(id=u.id, name=getattr(u, "full_name", None), email=getattr(u, "email", None)) for u in rows]


@router.get("/routing-preview")
def routing_preview(
    category_id: Optional[UUID] = Query(None),
    subcategory_id: Optional[UUID] = Query(None),
    ticket_type: str = Query("incident"),
    organization_id: Optional[UUID] = Query(None),
    priority: str = Query("medium"),
    sla_package_id: Optional[UUID] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """READ-ONLY intake intelligence for the create page. Given the in-progress
    classification, returns the team a ticket WOULD route to, whether the current user
    is on that team (and may self-claim), and a real SLA forecast — without creating
    anything. Declared before /{ticket_id} (literal-first). Any authenticated user."""
    if priority not in _PRIORITIES:
        priority = TicketPriority.MEDIUM.value
    # A transient, un-persisted ticket carrying just enough to drive the matcher.
    # subcategory_id matters: rules can condition on it and lanes route by it, so a
    # preview without it would promise the wrong lane for subcategory-scoped routing.
    probe = SdTicket(
        category_id=category_id, subcategory_id=subcategory_id, ticket_type=ticket_type,
        organization_id=organization_id, raised_by_user_id=user.id,
    )
    routed = match_route(db, probe)
    team = routed.get("team")
    queue = routed.get("queue")

    team_members = _team_members_of(db, team.id) if team else set()
    you_are_on_team = bool(team) and (user.id in team_members)
    # can_self_assign mirrors _can_work: agent OR member of the handling team.
    if team:
        probe.team_id = team.id
    can_self_assign = _can_work(db, probe, user)

    # The gate must consider EVERY team that handles this type/category, not just the one
    # that wins routing — two teams (e.g. Tier 1 + Tier 2) can both own a type, and being
    # on either must count. handling_member_ids is the union of those teams' rosters.
    handling = teams_handling(db, ticket_type, category_id)
    handling_member_ids = set()
    for tm in handling:
        handling_member_ids |= _team_members_of(db, tm.id)
    you_handle_type = user.id in handling_member_ids

    pkg = resolve_sla_package(db, sla_package_id, organization_id)
    rd, rsd = sla_util.compute_deadlines(pkg, priority)
    return {
        "team_id": str(team.id) if team else None,
        "team_name": team.name if team else None,
        "queue_name": queue.name if queue else None,
        "you_are_on_team": you_are_on_team,
        "can_self_assign": bool(can_self_assign),
        # On ANY team that handles this type/category (the gate uses this, not the single
        # routed team — so a Tier 2 member isn't blocked just because Tier 1 sorts first).
        "you_handle_type": bool(you_handle_type),
        # Member user-ids of the handling team(s) — lets the create page validate that a
        # reporting-manager's chosen assignee is on a team that handles this.
        "team_member_ids": [str(m) for m in team_members],
        "handling_member_ids": [str(m) for m in handling_member_ids],
        "sla_package_name": pkg.name if pkg else None,
        "sla_response_eta": rd.isoformat() if rd else None,
        "sla_resolution_eta": rsd.isoformat() if rsd else None,
    }


@router.get("/categories", response_model=list[CategoryResponse])
def my_categories(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Active ticket categories for the self-service create form. Mirrors the admin
    categories list but readable by ANY employee — fixes the empty Category dropdown
    (the admin /support-desk/categories list is gated to support agents → 403)."""
    return (db.query(SdCategory)
            .filter(SdCategory.is_deleted == False, SdCategory.is_active == True)  # noqa: E712
            .order_by(SdCategory.sort_order, SdCategory.name).all())


_SELF_SORT = {
    "created_at": SdTicket.created_at,
    "updated_at": SdTicket.updated_at,
    "ticket_number": SdTicket.ticket_number,
    "subject": SdTicket.subject,
    "status": SdTicket.status,
    "resolution_due_at": SdTicket.resolution_due_at,
    # Breached desk: oldest-breach-first == sort by breach stamp asc (overage descends).
    "sla_resolution_breached_at": SdTicket.sla_resolution_breached_at,
    "sla_response_breached_at": SdTicket.sla_response_breached_at,
    # Reopened desk: latest cycle first / most-bounced first.
    "last_reopened_at": SdTicket.last_reopened_at,
    "reopened_count": SdTicket.reopened_count,
    # Resolved desk: newest-resolution-first, rating, and time-to-resolve (seconds).
    "resolved_at": SdTicket.resolved_at,
    "closed_at": SdTicket.closed_at,
    "csat_score": SdTicket.csat_score,
    "ttr": func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at),
}


def _apply_self_scope(q, scope: str):
    """Mirror the agent scopes, but self-scoped (the user's own tickets). So the
    user-panel operational pages (Open/Pending/Overdue/…) populate for a plain
    employee with THEIR tickets — no 403, no empty page."""
    if scope in ("open", "in_progress"):
        return q.filter(SdTicket.status.in_([TicketStatus.OPEN.value, TicketStatus.IN_PROGRESS.value]))
    if scope == "pending":
        return q.filter(SdTicket.status.in_([TicketStatus.PENDING_CUSTOMER.value, TicketStatus.PENDING_VENDOR.value]))
    if scope == "pending_customer":
        return q.filter(SdTicket.status == TicketStatus.PENDING_CUSTOMER.value)
    if scope == "pending_vendor":
        return q.filter(SdTicket.status == TicketStatus.PENDING_VENDOR.value)
    if scope == "on_hold":
        return q.filter(SdTicket.status == TicketStatus.ON_HOLD.value)
    if scope == "escalated":
        return q.filter(SdTicket.is_escalated == True)  # noqa: E712
    if scope == "critical":
        return q.filter(SdTicket.priority == TicketPriority.CRITICAL.value)
    if scope == "sla_breached":
        return q.filter(or_(SdTicket.sla_response_breached == True, SdTicket.sla_resolution_breached == True))  # noqa: E712
    if scope == "due_soon":
        # "Prevent the NEXT breach" rail — clock running, resolution un-missed, due ≤2h.
        nowt = sla_util.now_utc()
        return q.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES),
                        SdTicket.sla_paused_since.is_(None),
                        SdTicket.resolved_at.is_(None),
                        SdTicket.sla_resolution_breached == False,  # noqa: E712
                        SdTicket.resolution_due_at.isnot(None),
                        SdTicket.resolution_due_at > nowt,
                        SdTicket.resolution_due_at <= nowt + timedelta(hours=2))
    if scope == "overdue":
        return q.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES),
                        SdTicket.sla_paused_since.is_(None),   # paused = frozen clock, not overdue
                        SdTicket.resolution_due_at.isnot(None),
                        SdTicket.resolution_due_at < sla_util.now_utc())
    if scope == "reopened":
        return q.filter(SdTicket.reopened_count > 0)
    if scope == "resolved":
        return q.filter(SdTicket.status.in_([TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value]))
    if scope == "closed":
        return q.filter(SdTicket.status == TicketStatus.CLOSED.value)
    return q  # all / my / unassigned → everything that's mine


@router.get("/", response_model=TicketListResponse)
def list_my_tickets(
    scope: Optional[str] = Query(None),
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    include_major: bool = Query(False),
    breach_kind: Optional[str] = Query(None, description="With scope=sla_breached: response|resolution|both"),
    overdue_kind: Optional[str] = Query(None, description="With scope=overdue: any|response|resolution (default resolution — legacy)"),
    missing_rca: Optional[bool] = Query(None, description="With scope=sla_breached: only tickets lacking BOTH breach_reason and rca_summary"),
    active_only: Optional[bool] = Query(None, description="Exclude terminal (resolved/closed) tickets"),
    reopen_source: Optional[str] = Query(None, description="Reopened desk: requester|agent|portal|auto"),
    chronic: Optional[bool] = Query(None, description="Reopened desk: only repeat offenders (reopened_count >= chronic threshold)"),
    reopened_from: Optional[datetime] = Query(None, description="Reopened desk: last_reopened_at >= this instant"),
    reopened_to: Optional[datetime] = Query(None, description="Reopened desk: last_reopened_at <= this instant"),
    resolved_from: Optional[datetime] = Query(None, description="Resolved desk: resolved_at >= this instant"),
    resolved_to: Optional[datetime] = Query(None, description="Resolved desk: resolved_at <= this instant"),
    resolution_code: Optional[str] = Query(None, description="Resolved desk: filter by resolution code"),
    resolution_category: Optional[str] = Query(None, description="Resolved desk: filter by root cause"),
    resolved_by: Optional[UUID] = Query(None, description="Resolved desk: who recorded the fix"),
    csat: Optional[str] = Query(None, description="Resolved desk: rated|unrated|low (low = score <= 2)"),
    pending_close: Optional[bool] = Query(None, description="Resolved desk: only the pre-close shelf (status=resolved)"),
    include_closed: Optional[bool] = Query(None, description="Accepted for parity; the self resolved scope already includes closed"),
    closed_from: Optional[datetime] = Query(None, description="Closed desk: closed_at >= this instant"),
    closed_to: Optional[datetime] = Query(None, description="Closed desk: closed_at <= this instant"),
    close_source: Optional[str] = Query(None, description="Closed desk: auto_sweep|manual|merged|withdrawn|no_response"),
    q: Optional[str] = None,
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        or_(SdTicket.raised_by_user_id == user.id, SdTicket.assigned_agent_id == user.id,
            SdTicket.collaborators.op('@>')(func.jsonb_build_array(str(user.id)))),
    )
    if scope == "critical" and include_major:
        # War-room widening: my critical board also shows my major incidents at any priority.
        query = query.filter(or_(SdTicket.priority == TicketPriority.CRITICAL.value,
                                 SdTicket.is_major_incident == True))  # noqa: E712
    elif scope == "overdue":
        # `overdue_kind` widens to the response clock (any|response); default = legacy resolution.
        query = apply_overdue_scope(query, overdue_kind)
    elif scope and scope not in ("all", "my", "unassigned"):
        query = _apply_self_scope(query, scope)
    # Auto-close sweep: opening the resolved view closes any ticket whose 3-day reopen
    # window lapsed, so the pre-close shelf is honest (idempotent; commits itself;
    # the cron covers unattended desks — same precedent as the hold-expiry sweep).
    if scope in ("resolved", "closed"):
        try:
            from app.routers.support_desk._common import auto_close_due_tickets
            auto_close_due_tickets(db)
        except Exception:
            db.rollback()
    # Breached-desk refinements (mirror the agent list so both panels paginate correctly).
    if breach_kind == "response":
        query = query.filter(SdTicket.sla_response_breached == True,   # noqa: E712
                             SdTicket.sla_resolution_breached == False)  # noqa: E712
    elif breach_kind == "resolution":
        query = query.filter(SdTicket.sla_resolution_breached == True,  # noqa: E712
                             SdTicket.sla_response_breached == False)   # noqa: E712
    elif breach_kind == "both":
        query = query.filter(SdTicket.sla_response_breached == True,    # noqa: E712
                             SdTicket.sla_resolution_breached == True)  # noqa: E712
    if missing_rca:
        query = query.filter(or_(SdTicket.breach_reason.is_(None), SdTicket.breach_reason == ""),
                             or_(SdTicket.rca_summary.is_(None), SdTicket.rca_summary == ""))
    if active_only:
        query = query.filter(SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
    # Reopened-desk refinements (mirror the agent list so both panels paginate correctly).
    if reopen_source:
        query = query.filter(SdTicket.reopen_source == reopen_source)
    if chronic:
        query = query.filter(SdTicket.reopened_count >= CHRONIC_REOPEN_THRESHOLD)
    if reopened_from:
        query = query.filter(SdTicket.last_reopened_at.isnot(None),
                             SdTicket.last_reopened_at >= reopened_from)
    if reopened_to:
        query = query.filter(SdTicket.last_reopened_at.isnot(None),
                             SdTicket.last_reopened_at <= reopened_to)
    # Resolved-desk refinements (mirror the agent list so both panels paginate correctly).
    if resolved_from:
        query = query.filter(SdTicket.resolved_at.isnot(None),
                             SdTicket.resolved_at >= resolved_from)
    if resolved_to:
        query = query.filter(SdTicket.resolved_at.isnot(None),
                             SdTicket.resolved_at <= resolved_to)
    if resolution_code:
        query = query.filter(SdTicket.resolution_code == resolution_code)
    if resolution_category:
        query = query.filter(SdTicket.resolution_category == resolution_category)
    if resolved_by:
        query = query.filter(func.coalesce(SdTicket.resolved_by_id, SdTicket.assigned_agent_id) == resolved_by)
    if csat == "rated":
        query = query.filter(SdTicket.csat_score.isnot(None))
    elif csat == "unrated":
        query = query.filter(SdTicket.csat_score.is_(None))
    elif csat == "low":
        query = query.filter(SdTicket.csat_score <= 2)
    if pending_close:
        query = query.filter(SdTicket.status == TicketStatus.RESOLVED.value)
    # Closed-desk refinements (mirror the agent list so both panels paginate correctly).
    if closed_from:
        query = query.filter(SdTicket.closed_at.isnot(None),
                             SdTicket.closed_at >= closed_from)
    if closed_to:
        query = query.filter(SdTicket.closed_at.isnot(None),
                             SdTicket.closed_at <= closed_to)
    query = apply_close_source(query, close_source)
    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))

    total = query.count()
    col = _SELF_SORT.get(sort_by, SdTicket.created_at)
    col = col.asc() if (sort_dir or "desc").lower() == "asc" else col.desc()
    items = query.order_by(col, SdTicket.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    enrich_tickets(db, items)
    # Personal-queue vendor privacy is PER-TICKET, matching the detail view: vendor
    # internals show only on rows the viewer can actually WORK (_can_work). The old
    # global _is_agent(user) check leaked vendor names to a desk agent who was merely
    # the REQUESTER on another team's ticket — here they are a requester like any other.
    ctx = _team_context(db, user) if _is_agent(user) else None
    rows = []
    for t in items:
        r = TicketResponse.model_validate(t)
        if ctx is None or not _can_work(db, t, user, ctx):
            _scrub_vendor_internals(r)
        rows.append(r)
    return TicketListResponse(items=rows, total=total, page=page, limit=limit)


@router.get("/team-tickets", response_model=TicketListResponse)
def list_my_team_tickets(
    scope: Optional[str] = Query(None),
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    report_id: Optional[UUID] = Query(None, description="Drill down to ONE direct report — tickets assigned to (or raised by) that report. 403 if the id isn't your report."),
    q: Optional[str] = None,
    sort_by: str = Query("updated_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The user-panel team board: tickets belonging to a support team the user is on
    (member or lead) PLUS tickets raised-by/assigned-to the user's direct reports.
    Empty for users with no team and no reports — no 403, graceful. Before /{ticket_id}.

    ``report_id`` narrows to a single direct report (a reporting-manager drill-down; the
    id must be one of the caller's reports or a 403)."""
    ctx = _team_context(db, user)
    scope_ids = list(ctx["scope_ids"])
    team_ids = ctx["team_ids"]
    if len(scope_ids) <= 1 and not team_ids:   # only {me} — not on a team, no reports
        return TicketListResponse(items=[], total=0, page=page, limit=limit)
    if report_id is not None:
        if report_id not in ctx["reports"]:
            raise HTTPException(403, "That user isn't one of your direct reports.")
        conds = [SdTicket.assigned_agent_id == report_id, SdTicket.raised_by_user_id == report_id]
    else:
        conds = [SdTicket.raised_by_user_id.in_(scope_ids), SdTicket.assigned_agent_id.in_(scope_ids)]
        if team_ids:
            conds.append(SdTicket.team_id.in_(team_ids))
    query = db.query(SdTicket).filter(SdTicket.is_deleted == False, or_(*conds))  # noqa: E712
    if scope and scope not in ("all", "my", "unassigned"):
        query = _apply_self_scope(query, scope)
    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))
    total = query.count()
    col = _SELF_SORT.get(sort_by, SdTicket.updated_at)
    col = col.asc() if (sort_dir or "desc").lower() == "asc" else col.desc()
    items = query.order_by(col, SdTicket.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    enrich_tickets(db, items)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in items],
        total=total, page=page, limit=limit,
    )


@router.get("/reports-overview")
def reports_overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Reporting-line oversight for a manager: each DIRECT REPORT with their live support
    workload (open / breached / critical / due-soon / aging + resolved-today), plus rolled
    totals. Scoped strictly to the HR reporting line (``ctx['reports']``) — NOT support-team
    membership — so a manager sees their people's queues even across teams they don't sit on.
    Empty (is_manager=False) for a caller with no reports. Drill into one report's tickets via
    GET /me/tickets/team-tickets?report_id=<uid>."""
    ctx = _team_context(db, user)
    reports = list(ctx["reports"])
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    soon = now + timedelta(hours=4)
    d7 = now - timedelta(days=7)
    terminal = list(TERMINAL_TICKET_STATUSES)
    if not reports:
        return {"is_manager": False, "generated_at": now, "reports": [],
                "totals": {"reports": 0, "open": 0, "breached": 0, "critical": 0,
                           "due_soon": 0, "aging": 0, "resolved_today": 0}}

    is_active = SdTicket.status.notin_(terminal)

    def _sum(cond):
        return func.sum(case((cond, 1), else_=0))

    rows = (db.query(
        SdTicket.assigned_agent_id.label("uid"),
        _sum(is_active).label("open"),
        _sum(and_(is_active, or_(SdTicket.sla_resolution_breached == True,  # noqa: E712
                                 SdTicket.sla_response_breached == True))).label("breached"),  # noqa: E712
        _sum(and_(is_active, or_(SdTicket.priority == "critical",
                                 SdTicket.is_major_incident == True))).label("critical"),  # noqa: E712
        _sum(and_(is_active, SdTicket.sla_resolution_breached == False,  # noqa: E712
                  SdTicket.resolution_due_at.isnot(None),
                  SdTicket.resolution_due_at <= soon,
                  SdTicket.resolution_due_at > now)).label("due_soon"),
        _sum(and_(is_active, SdTicket.created_at < d7)).label("aging"),
        _sum(and_(SdTicket.status == TicketStatus.RESOLVED.value,
                  SdTicket.resolved_at >= sod)).label("resolved_today"),
    ).filter(SdTicket.is_deleted == False,  # noqa: E712
             SdTicket.merged_into_id.is_(None),
             SdTicket.assigned_agent_id.in_(reports))
        .group_by(SdTicket.assigned_agent_id).all())

    by_uid = {str(r.uid): r for r in rows}
    names = _user_names(db, reports)
    statuses = _statuses_of(db, reports)
    out_reports = []
    tot = {"reports": len(reports), "open": 0, "breached": 0, "critical": 0,
           "due_soon": 0, "aging": 0, "resolved_today": 0}
    for uid in reports:
        r = by_uid.get(str(uid))
        entry = {
            "user_id": str(uid), "name": names.get(str(uid)) or "Agent",
            "status": statuses.get(str(uid), "online"),
            "open": int(getattr(r, "open", 0) or 0),
            "breached": int(getattr(r, "breached", 0) or 0),
            "critical": int(getattr(r, "critical", 0) or 0),
            "due_soon": int(getattr(r, "due_soon", 0) or 0),
            "aging": int(getattr(r, "aging", 0) or 0),
            "resolved_today": int(getattr(r, "resolved_today", 0) or 0),
        }
        for k in ("open", "breached", "critical", "due_soon", "aging", "resolved_today"):
            tot[k] += entry[k]
        out_reports.append(entry)
    out_reports.sort(key=lambda e: (-e["breached"], -e["critical"], -e["open"], e["name"].lower()))
    return {"is_manager": True, "generated_at": now, "reports": out_reports, "totals": tot}


def _statuses_of(db: Session, user_ids) -> dict:
    """{user_id_str: presence status} — absent row reads as 'online' (mirror of queue_ops)."""
    from app.models.support_desk.workspace import SdAgentStatus
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = db.query(SdAgentStatus).filter(SdAgentStatus.user_id.in_(list(ids))).all()
    return {str(r.user_id): r.status for r in rows}


@router.get("/command-center", response_model=TicketListResponse)
def list_command_center(
    scope: Optional[str] = Query(None),
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    ticket_type: Optional[str] = None,
    include_major: bool = Query(False),
    overdue_kind: Optional[str] = Query(None, description="With scope=overdue: any|response|resolution (default resolution — legacy)"),
    assigned_agent_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    q: Optional[str] = None,
    sort_by: str = Query("updated_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(150, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Team Operations Command Center — the All-Tickets agent surface. Hard-scoped to the
    teams the caller is on (member/lead) + the untriaged triage pool routing to those teams
    + the caller's own involvement. UI params can only NARROW within scope: an out-of-scope
    team_id / assigned_agent_id is silently ignored (never honoured). Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    # Hold-expiry sweep: opening the On-Hold dock releases any hold whose hold_until has
    # passed (auto-resume; SLA un-freezes). Mirrors the admin list; cron covers the rest.
    if scope == "on_hold":
        try:
            auto_resume_expired_holds(db)
        except Exception:
            db.rollback()
    # Escalation sweeps: opening the Escalated desk auto-escalates SLA-resolution-breached,
    # owned, actively-worked tickets once + nudges lapsed escalation-response clocks.
    # Mirrors the admin list hook; cron covers unattended desks.
    if scope == "escalated":
        try:
            from app.utils.support_desk.escalation import (
                sweep_sla_breach_escalation, sweep_escalation_response_overdue)
            seal = None if is_su else _command_center_filter(user, ctx)
            if sweep_sla_breach_escalation(db, team_cond=seal):
                db.commit()
            sweep_escalation_response_overdue(db)   # commits itself when it nudged
        except Exception:
            db.rollback()
    query = db.query(SdTicket).filter(SdTicket.is_deleted == False)  # noqa: E712
    if not is_su:  # superusers see the whole desk; agents are team-sealed
        query = query.filter(_command_center_filter(user, ctx))
    if scope == "my":
        query = query.filter(SdTicket.assigned_agent_id == user.id)
    elif scope == "unassigned":
        query = query.filter(SdTicket.assigned_agent_id.is_(None), SdTicket.status.in_(OPEN_TICKET_STATUSES))
    elif scope == "critical" and include_major:
        # War-room widening: a major incident may run at any priority — never hide one here.
        query = query.filter(or_(SdTicket.priority == TicketPriority.CRITICAL.value,
                                 SdTicket.is_major_incident == True))  # noqa: E712
    elif scope == "overdue":
        # `overdue_kind` widens to the response clock (any|response); default = legacy resolution.
        query = apply_overdue_scope(query, overdue_kind)
    elif scope and scope != "all":
        query = _apply_self_scope(query, scope)
    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if ticket_type:
        query = query.filter(SdTicket.ticket_type == ticket_type)
    # Narrow-only: honour a team/agent filter ONLY when within the caller's scope (superuser anywhere).
    if team_id and (is_su or team_id in ctx["team_ids"]):
        query = query.filter(SdTicket.team_id == team_id)
    if assigned_agent_id and (is_su or assigned_agent_id in (ctx["member_ids"] | ctx["reports"] | {user.id})):
        query = query.filter(SdTicket.assigned_agent_id == assigned_agent_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))
    total = query.count()
    col = _SELF_SORT.get(sort_by, SdTicket.updated_at)
    col = col.asc() if (sort_dir or "desc").lower() == "asc" else col.desc()
    items = query.order_by(col, SdTicket.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    enrich_tickets(db, items)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in items],
        total=total, page=page, limit=limit,
    )


@router.get("/command-center/stats", response_model=CommandCenterStats)
def command_center_stats(mine: bool = Query(False, description="Aggregate only tickets ASSIGNED TO ME (user-portal personal desks)"),
                         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Team-scoped command-center aggregate (SAME filter as the list, so KPIs reconcile).
    Reuses the heuristic workbench for KPIs + insights, then layers the F1 flag board,
    fastest-lap (top resolver today) and per-agent squad load. Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    base = db.query(SdTicket).filter(SdTicket.is_deleted == False)  # noqa: E712
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        base = base.filter(SdTicket.assigned_agent_id == user.id)
    elif not is_su:  # superusers aggregate the whole desk; agents are team-sealed
        base = base.filter(_command_center_filter(user, ctx))
    out = CommandCenterStats(**compute_workbench(db, base, actor=user).model_dump())
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    open_set = OPEN_TICKET_STATUSES
    out.total = base.count()
    out.unassigned_in_scope = base.filter(SdTicket.assigned_agent_id.is_(None),
                                          SdTicket.status.in_(open_set)).count()
    out.triage_pool = base.filter(SdTicket.team_id.is_(None), SdTicket.status.in_(open_set)).count()
    out.breaching, out.due_soon = out.sla_breached, out.sla_risk
    out.flag_red, out.flag_amber, out.flag_safety_car = out.sla_breached, out.sla_risk, out.on_hold
    out.flag_green = max(0, out.total_active - out.sla_breached - out.sla_risk)
    # Fastest lap — top resolver today.
    fl = (base.filter(SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= sod,
                      SdTicket.assigned_agent_id.isnot(None))
          .with_entities(SdTicket.assigned_agent_id, func.count(SdTicket.id))
          .group_by(SdTicket.assigned_agent_id).order_by(func.count(SdTicket.id).desc()).first())
    # Squad load — per-agent open/breaching/critical. Agents get their team roster; a
    # superuser gets the desk's busiest agents (top 12 by open load).
    sq = (base.filter(SdTicket.assigned_agent_id.isnot(None),
                      SdTicket.status.in_(open_set | {TicketStatus.ON_HOLD.value}))
          .with_entities(
              SdTicket.assigned_agent_id, func.count(SdTicket.id),
              func.sum(case((or_(SdTicket.sla_response_breached == True,  # noqa: E712
                                 SdTicket.sla_resolution_breached == True), 1), else_=0)),
              func.sum(case((SdTicket.priority == TicketPriority.CRITICAL.value, 1), else_=0))))
    rows = []
    if is_su:
        rows = sq.group_by(SdTicket.assigned_agent_id).order_by(func.count(SdTicket.id).desc()).limit(12).all()
    elif ctx["member_ids"]:
        rows = (sq.filter(SdTicket.assigned_agent_id.in_(list(ctx["member_ids"])))
                .group_by(SdTicket.assigned_agent_id).order_by(func.count(SdTicket.id).desc()).limit(20).all())
    name_ids = [r[0] for r in rows] + ([fl[0]] if fl else [])
    names = _user_names(db, name_ids) if name_ids else {}
    squad = [SquadLoad(agent_id=aid, name=names.get(str(aid)),
                       open=int(o or 0), breaching=int(b or 0), critical=int(c or 0))
             for aid, o, b, c in rows]
    squad.sort(key=lambda s: (-s.open, -s.breaching))
    out.squad = squad
    if fl:
        out.fastest_lap = FastestLap(agent_id=fl[0], name=names.get(str(fl[0])), count=int(fl[1] or 0))
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/critical/stats", response_model=CriticalStats)
def critical_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """War-room aggregate for the Critical board — same team seal as the command-center
    list (superuser = whole desk), computed over priority=critical ∪ major incidents so
    the board's lenses reconcile with its working set. Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    crit_cond = or_(SdTicket.priority == TicketPriority.CRITICAL.value,
                    SdTicket.is_major_incident == True)  # noqa: E712
    base = db.query(SdTicket).filter(SdTicket.is_deleted == False, crit_cond)  # noqa: E712
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        base = base.filter(SdTicket.assigned_agent_id == user.id)
    elif not is_su:
        base = base.filter(_command_center_filter(user, ctx))

    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d30 = now - timedelta(days=30)
    terminal = list(TERMINAL_TICKET_STATUSES)
    active = base.filter(SdTicket.status.notin_(terminal))

    out = CriticalStats()
    out.active_critical = active.count()
    out.major_incidents = active.filter(SdTicket.is_major_incident == True).count()  # noqa: E712
    out.breaching = active.filter(or_(SdTicket.sla_response_breached == True,  # noqa: E712
                                      SdTicket.sla_resolution_breached == True)).count()  # noqa: E712
    out.due_soon = active.filter(SdTicket.sla_paused_since.is_(None),
                                 SdTicket.resolution_due_at.isnot(None),
                                 SdTicket.resolution_due_at > now,
                                 SdTicket.resolution_due_at <= now + timedelta(hours=2)).count()
    out.unacked = active.filter(SdTicket.acknowledged_at.is_(None)).count()
    out.update_overdue = active.filter(SdTicket.next_update_due_at.isnot(None),
                                       SdTicket.next_update_due_at < now).count()
    out.no_owner = active.filter(SdTicket.assigned_agent_id.is_(None)).count()
    oldest = active.with_entities(func.min(SdTicket.created_at)).scalar()
    if oldest is not None:
        out.oldest_age_minutes = max(0, int((now - sla_util._aware(oldest)).total_seconds() // 60))
    # MTTA / MTTR — rolling 30-day means (created → acked / created → resolved).
    mtta = (base.filter(SdTicket.acknowledged_at.isnot(None), SdTicket.acknowledged_at >= d30)
            .with_entities(func.avg(func.extract("epoch", SdTicket.acknowledged_at - SdTicket.created_at)))
            .scalar())
    out.mtta_minutes = round(float(mtta) / 60.0, 1) if mtta is not None else None
    mttr = (base.filter(SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30,
                        SdTicket.merged_into_id.is_(None))
            .with_entities(func.avg(func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)))
            .scalar())
    out.mttr_minutes = round(float(mttr) / 60.0, 1) if mttr is not None else None
    out.resolved_today = base.filter(SdTicket.merged_into_id.is_(None),
                                     SdTicket.resolved_at.isnot(None),
                                     SdTicket.resolved_at >= sod).count()
    out.ack_coverage = (int(round(100 * (out.active_critical - out.unacked) / out.active_critical))
                        if out.active_critical else 100)
    # PIR gap — terminal criticals from the last 30 days with no root-cause record.
    out.missing_rca = base.filter(SdTicket.status.in_(terminal),
                                  SdTicket.merged_into_id.is_(None),
                                  SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30,
                                  or_(SdTicket.rca_summary.is_(None), SdTicket.rca_summary == "")).count()
    # Business-impact composition of the active board.
    out.by_business_impact = {(bi or "unset"): int(c or 0) for bi, c in
                              active.with_entities(SdTicket.business_impact, func.count(SdTicket.id))
                              .group_by(SdTicket.business_impact).all()}
    # Responder load — who is carrying the active criticals.
    sq = (active.filter(SdTicket.assigned_agent_id.isnot(None))
          .with_entities(
              SdTicket.assigned_agent_id, func.count(SdTicket.id),
              func.sum(case((or_(SdTicket.sla_response_breached == True,  # noqa: E712
                                 SdTicket.sla_resolution_breached == True), 1), else_=0)),
              func.sum(case((SdTicket.is_major_incident == True, 1), else_=0)))  # noqa: E712
          .group_by(SdTicket.assigned_agent_id)
          .order_by(func.count(SdTicket.id).desc()).limit(16).all())
    names = _user_names(db, [r[0] for r in sq]) if sq else {}
    out.squad = [SquadLoad(agent_id=aid, name=names.get(str(aid)),
                           open=int(o or 0), breaching=int(b or 0), critical=int(m or 0))
                 for aid, o, b, m in sq]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/escalated/stats", response_model=EscalationStats)
def escalated_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Thermal-Updraft aggregate for the Escalated desk — same team seal as the
    command-center list (superuser = whole desk), computed over is_escalated tickets so
    the board's lenses reconcile with its working set. Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    seal = None if is_su else _command_center_filter(user, ctx)
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        seal = (SdTicket.assigned_agent_id == user.id)
    base = db.query(SdTicket).filter(SdTicket.is_deleted == False,  # noqa: E712
                                     SdTicket.is_escalated == True)  # noqa: E712
    if seal is not None:
        base = base.filter(seal)

    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d30 = now - timedelta(days=30)
    terminal = list(TERMINAL_TICKET_STATUSES)
    active = base.filter(SdTicket.status.notin_(terminal))

    out = EscalationStats()
    out.active_escalations = active.count()
    # Tier composition — bucket unbounded levels into 1 / 2 / 3+.
    lvl_rows = (active.with_entities(SdTicket.escalation_level, func.count(SdTicket.id))
                .group_by(SdTicket.escalation_level).all())
    by_level: dict[str, int] = {}
    for lvl, c in lvl_rows:
        key = "3+" if (lvl or 0) >= 3 else str(max(1, int(lvl or 1)))
        by_level[key] = by_level.get(key, 0) + int(c or 0)
    out.by_level = by_level
    out.by_type = {(tp or "unset"): int(c or 0) for tp, c in
                   active.with_entities(SdTicket.escalation_type, func.count(SdTicket.id))
                   .group_by(SdTicket.escalation_type).all()}
    out.by_reason_code = {(rc or "unset"): int(c or 0) for rc, c in
                          active.with_entities(SdTicket.escalation_reason_code, func.count(SdTicket.id))
                          .group_by(SdTicket.escalation_reason_code).all()}
    out.unacked = active.filter(SdTicket.escalation_acknowledged_at.is_(None)).count()
    out.esc_response_overdue = active.filter(
        SdTicket.escalation_acknowledged_at.is_(None),
        SdTicket.escalation_response_due_at.isnot(None),
        SdTicket.escalation_response_due_at < now).count()
    out.breaching_sla = active.filter(or_(SdTicket.sla_response_breached == True,  # noqa: E712
                                          SdTicket.sla_resolution_breached == True)).count()  # noqa: E712
    out.no_owner = active.filter(SdTicket.assigned_agent_id.is_(None)).count()
    out.auto_escalated_count = active.filter(SdTicket.auto_escalated_at.isnot(None)).count()
    # Breach candidates — breached, NOT escalated, actively worked. The no-write lens the
    # auto-sweep leaves behind (unowned tickets + response breaches, per config).
    cand = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.is_escalated == False,  # noqa: E712
        SdTicket.status.notin_(terminal),
        or_(SdTicket.sla_response_breached == True,  # noqa: E712
            SdTicket.sla_resolution_breached == True))  # noqa: E712
    if seal is not None:
        cand = cand.filter(seal)
    out.sla_breach_candidates = cand.count()
    oldest = active.with_entities(func.min(SdTicket.escalated_at)).scalar()
    if oldest is not None:
        out.oldest_escalation_age_minutes = max(0, int((now - sla_util._aware(oldest)).total_seconds() // 60))
    # Dwell — mean time-at-current-tier across the active board.
    dwell = (active.filter(SdTicket.escalated_at.isnot(None))
             .with_entities(func.avg(func.extract("epoch",
                            sa_literal(now) - SdTicket.escalated_at))).scalar())
    out.avg_dwell_minutes = round(float(dwell) / 60.0, 1) if dwell is not None else None
    # eMTTA — rolling 30-day mean escalated_at → escalation_acknowledged_at.
    emtta = (base.filter(SdTicket.escalation_acknowledged_at.isnot(None),
                         SdTicket.escalation_acknowledged_at >= d30,
                         SdTicket.escalated_at.isnot(None))
             .with_entities(func.avg(func.extract("epoch",
                            SdTicket.escalation_acknowledged_at - SdTicket.escalated_at)))
             .scalar())
    out.emtta_minutes = round(float(emtta) / 60.0, 1) if emtta is not None else None
    out.de_escalated_today = (
        db.query(func.count(func.distinct(SdTicketActivity.ticket_id)))
        .select_from(SdTicketActivity)
        .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
        .filter(SdTicketActivity.action == "de_escalated",
                SdTicketActivity.created_at >= sod,
                SdTicket.is_deleted == False,  # noqa: E712
                *( [seal] if seal is not None else [] ))
        .scalar() or 0)
    out.resolved_today = base.filter(SdTicket.merged_into_id.is_(None),
                                     SdTicket.resolved_at.isnot(None),
                                     SdTicket.resolved_at >= sod).count()
    out.ack_coverage = (int(round(100 * (out.active_escalations - out.unacked) / out.active_escalations))
                        if out.active_escalations else 100)
    # Squad — who is carrying the active escalations (critical column = tier 2+ count).
    sq = (active.filter(SdTicket.assigned_agent_id.isnot(None))
          .with_entities(
              SdTicket.assigned_agent_id, func.count(SdTicket.id),
              func.sum(case((or_(SdTicket.sla_response_breached == True,  # noqa: E712
                                 SdTicket.sla_resolution_breached == True), 1), else_=0)),
              func.sum(case((SdTicket.escalation_level >= 2, 1), else_=0)))
          .group_by(SdTicket.assigned_agent_id)
          .order_by(func.count(SdTicket.id).desc()).limit(16).all())
    names = _user_names(db, [r[0] for r in sq]) if sq else {}
    out.squad = [SquadLoad(agent_id=aid, name=names.get(str(aid)),
                           open=int(o or 0), breaching=int(b or 0), critical=int(hi or 0))
                 for aid, o, b, hi in sq]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/breached/stats", response_model=BreachedStats)
def breached_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Time-Debt-Meter aggregate for the Breached desk — same team seal as the
    command-center list (superuser = whole desk). Runs the breach-flag sweep FIRST so
    idle tickets that silently passed their deadline are counted (lenses ≡ list).
    Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    seal = None if is_su else _command_center_filter(user, ctx)
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        seal = (SdTicket.assigned_agent_id == user.id)

    out = BreachedStats()
    # Sweep stale flags inside the caller's scope (idempotent; caller-commit contract).
    try:
        from app.utils.support_desk.breach import sweep_sla_breach_flags
        out.swept_now = sweep_sla_breach_flags(db, team_cond=seal)
        if out.swept_now:
            db.commit()
    except Exception:
        db.rollback()

    breach_cond = or_(SdTicket.sla_response_breached == True,   # noqa: E712
                      SdTicket.sla_resolution_breached == True)  # noqa: E712
    base = db.query(SdTicket).filter(SdTicket.is_deleted == False, breach_cond)  # noqa: E712
    if seal is not None:
        base = base.filter(seal)

    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d30 = now - timedelta(days=30)
    terminal = list(TERMINAL_TICKET_STATUSES)
    active = base.filter(SdTicket.status.notin_(terminal))

    out.active_breached = active.count()
    # Kind split over the active board (response-only / resolution-only / both).
    out.by_kind = {
        "response": active.filter(SdTicket.sla_response_breached == True,   # noqa: E712
                                  SdTicket.sla_resolution_breached == False).count(),  # noqa: E712
        "resolution": active.filter(SdTicket.sla_resolution_breached == True,  # noqa: E712
                                    SdTicket.sla_response_breached == False).count(),   # noqa: E712
        "both": active.filter(SdTicket.sla_response_breached == True,   # noqa: E712
                              SdTicket.sla_resolution_breached == True).count(),  # noqa: E712
    }
    out.by_priority = {(p or "unset"): int(c or 0) for p, c in
                       active.with_entities(SdTicket.priority, func.count(SdTicket.id))
                       .group_by(SdTicket.priority).all()}
    out.by_status = {(s or "unset"): int(c or 0) for s, c in
                     active.with_entities(SdTicket.status, func.count(SdTicket.id))
                     .group_by(SdTicket.status).all()}
    # Breach aging — bucketed on the worst (earliest) breach stamp, falling back to due.
    breached_ref = func.coalesce(SdTicket.sla_resolution_breached_at,
                                 SdTicket.sla_response_breached_at,
                                 SdTicket.resolution_due_at, SdTicket.response_due_at)
    age_case = case(
        (breached_ref >= now - timedelta(hours=2), "<2h"),
        (breached_ref >= now - timedelta(hours=8), "2-8h"),
        (breached_ref >= now - timedelta(hours=24), "8-24h"),
        else_=">24h")
    out.by_age = {k: int(c or 0) for k, c in
                  active.with_entities(age_case, func.count(SdTicket.id)).group_by(age_case).all()}
    out.unassigned_breached = active.filter(SdTicket.assigned_agent_id.is_(None)).count()
    out.not_escalated = active.filter(SdTicket.is_escalated == False).count()  # noqa: E712
    oldest = active.with_entities(func.min(breached_ref)).scalar()
    if oldest is not None:
        out.oldest_breach_age_minutes = max(0, int((now - sla_util._aware(oldest)).total_seconds() // 60))
    # Time debt — pause-aware overage over active resolution-breached tickets: measured to
    # NOW while the clock runs, frozen at sla_paused_since while paused (escalated-dwell
    # sa_literal(now) pattern).
    overage_ref = case((SdTicket.sla_paused_since.isnot(None), SdTicket.sla_paused_since),
                       else_=sa_literal(now))
    debt_q = active.filter(SdTicket.sla_resolution_breached == True,  # noqa: E712
                           SdTicket.resolution_due_at.isnot(None))
    debt = debt_q.with_entities(
        func.sum(func.extract("epoch", overage_ref - SdTicket.resolution_due_at)),
        func.avg(func.extract("epoch", overage_ref - SdTicket.resolution_due_at)),
        func.max(func.extract("epoch", overage_ref - SdTicket.resolution_due_at))).first()
    if debt and debt[0] is not None:
        out.total_debt_minutes = max(0, int(float(debt[0]) // 60))
        out.avg_overage_minutes = round(max(0.0, float(debt[1])) / 60.0, 1)
        out.max_overage_minutes = round(max(0.0, float(debt[2])) / 60.0, 1)
    # The prevention pair — next breaches to stop (mirrors scope=due_soon).
    risk_base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.status.notin_(terminal),
        SdTicket.sla_paused_since.is_(None),
        SdTicket.resolved_at.is_(None),
        SdTicket.sla_resolution_breached == False,  # noqa: E712
        SdTicket.resolution_due_at.isnot(None),
        SdTicket.resolution_due_at > now)
    if seal is not None:
        risk_base = risk_base.filter(seal)
    out.at_risk = risk_base.filter(SdTicket.resolution_due_at <= now + timedelta(hours=2)).count()
    out.imminent = risk_base.filter(SdTicket.resolution_due_at <= now + timedelta(minutes=30)).count()
    # Repair ledger — breached tickets the team still brought home.
    out.repaired_today = base.filter(SdTicket.merged_into_id.is_(None),
                                     SdTicket.resolved_at.isnot(None),
                                     SdTicket.resolved_at >= sod).count()
    overrun = (base.filter(SdTicket.merged_into_id.is_(None),
                           SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30,
                           SdTicket.resolution_due_at.isnot(None),
                           SdTicket.resolved_at > SdTicket.resolution_due_at)
               .with_entities(func.avg(func.extract("epoch",
                              SdTicket.resolved_at - SdTicket.resolution_due_at))).scalar())
    out.avg_repair_overrun_minutes = round(float(overrun) / 60.0, 1) if overrun is not None else None
    # RCA discipline — every breach owes a root cause.
    out.missing_rca = base.filter(
        or_(SdTicket.breach_reason.is_(None), SdTicket.breach_reason == ""),
        or_(SdTicket.rca_summary.is_(None), SdTicket.rca_summary == "")).count()
    total_base = base.count()
    out.rca_coverage = (int(round(100 * (total_base - out.missing_rca) / total_base))
                        if total_base else 100)
    # Squad — who is carrying the active breach debt (breaching = both-kind).
    sq = (active.filter(SdTicket.assigned_agent_id.isnot(None))
          .with_entities(
              SdTicket.assigned_agent_id, func.count(SdTicket.id),
              func.sum(case((and_(SdTicket.sla_response_breached == True,   # noqa: E712
                                  SdTicket.sla_resolution_breached == True), 1), else_=0)),  # noqa: E712
              func.sum(case((SdTicket.priority == TicketPriority.CRITICAL.value, 1), else_=0)))
          .group_by(SdTicket.assigned_agent_id)
          .order_by(func.count(SdTicket.id).desc()).limit(16).all())
    names = _user_names(db, [r[0] for r in sq]) if sq else {}
    out.squad = [SquadLoad(agent_id=aid, name=names.get(str(aid)),
                           open=int(o or 0), breaching=int(b or 0), critical=int(cr or 0))
                 for aid, o, b, cr in sq]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/overdue/stats", response_model=OverdueStats)
def overdue_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Gravity-Well aggregate for the Overdue recovery desk — same team seal as the
    command-center list (superuser = whole desk). Overdue = open statuses, clock RUNNING,
    past the resolution due date and/or past the response due date with no first reply.
    Runs the breach-flag sweep FIRST (idle past-due tickets get stamped) so the lenses
    reconcile with the list. Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    seal = None if is_su else _command_center_filter(user, ctx)
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        seal = (SdTicket.assigned_agent_id == user.id)

    out = OverdueStats()
    # Sweep stale breach flags inside the caller's scope (idempotent; caller-commit).
    try:
        from app.utils.support_desk.breach import sweep_sla_breach_flags
        out.swept_now = sweep_sla_breach_flags(db, team_cond=seal)
        if out.swept_now:
            db.commit()
    except Exception:
        db.rollback()

    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    res_over = and_(SdTicket.resolution_due_at.isnot(None), SdTicket.resolution_due_at < now)
    resp_over = and_(SdTicket.first_responded_at.is_(None),
                     SdTicket.response_due_at.isnot(None), SdTicket.response_due_at < now)
    base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.status.in_(OPEN_TICKET_STATUSES),
        SdTicket.sla_paused_since.is_(None),   # paused = frozen clock, not overdue
        or_(res_over, resp_over))
    if seal is not None:
        base = base.filter(seal)

    out.total = base.count()
    out.resolution_overdue = base.filter(res_over).count()
    out.response_overdue = base.filter(resp_over).count()
    out.both_overdue = base.filter(res_over, resp_over).count()
    out.unassigned = base.filter(SdTicket.assigned_agent_id.is_(None)).count()
    out.not_escalated = base.filter(SdTicket.is_escalated == False).count()  # noqa: E712
    out.critical = base.filter(SdTicket.priority == TicketPriority.CRITICAL.value).count()
    # Context lens: past-due-on-paper but the clock is legitimately frozen (pending/hold).
    frozen = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.status.in_(OPEN_TICKET_STATUSES),
        SdTicket.sla_paused_since.isnot(None),
        SdTicket.resolution_due_at.isnot(None), SdTicket.resolution_due_at < now)
    if seal is not None:
        frozen = frozen.filter(seal)
    out.frozen_excluded = frozen.count()

    out.by_priority = {(p or "unset"): int(c or 0) for p, c in
                       base.with_entities(SdTicket.priority, func.count(SdTicket.id))
                       .group_by(SdTicket.priority).all()}
    out.by_status = {(s or "unset"): int(c or 0) for s, c in
                     base.with_entities(SdTicket.status, func.count(SdTicket.id))
                     .group_by(SdTicket.status).all()}
    # Lateness ladder — bucketed on the governing missed clock (resolution wins over response).
    late_ref = case((res_over, SdTicket.resolution_due_at), else_=SdTicket.response_due_at)
    late_case = case(
        (late_ref >= now - timedelta(hours=1), "<1h"),
        (late_ref >= now - timedelta(hours=4), "1-4h"),
        (late_ref >= now - timedelta(hours=24), "4-24h"),
        (late_ref >= now - timedelta(days=3), "1-3d"),
        else_=">3d")
    out.by_late = {k: int(c or 0) for k, c in
                   base.with_entities(late_case, func.count(SdTicket.id)).group_by(late_case).all()}
    # Time owed — clock is running for everything in base, so lateness runs to NOW.
    owed = (base.filter(res_over)
            .with_entities(func.sum(func.extract("epoch", sa_literal(now) - SdTicket.resolution_due_at)),
                           func.avg(func.extract("epoch", sa_literal(now) - SdTicket.resolution_due_at)),
                           func.max(func.extract("epoch", sa_literal(now) - SdTicket.resolution_due_at))).first())
    if owed and owed[0] is not None:
        out.total_late_minutes = max(0, int(float(owed[0]) // 60))
        out.avg_late_minutes = round(max(0.0, float(owed[1])) / 60.0, 1)
        out.max_late_minutes = round(max(0.0, float(owed[2])) / 60.0, 1)
    worst = base.order_by(late_ref.asc()).first()
    if worst is not None:
        wdue = sla_util._aware(worst.resolution_due_at
                               if (worst.resolution_due_at and sla_util._aware(worst.resolution_due_at) < now)
                               else worst.response_due_at)
        out.oldest = OverdueWorst(
            ticket_id=worst.id, ticket_number=worst.ticket_number, subject=worst.subject,
            priority=worst.priority,
            late_minutes=max(0, int((now - wdue).total_seconds() // 60)) if wdue else 0)
    # Tipping point — the NEXT tickets to fall in (mirrors scope=due_soon).
    risk_base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.status.in_(OPEN_TICKET_STATUSES),
        SdTicket.sla_paused_since.is_(None),
        SdTicket.resolved_at.is_(None),
        SdTicket.sla_resolution_breached == False,  # noqa: E712
        SdTicket.resolution_due_at.isnot(None),
        SdTicket.resolution_due_at > now)
    if seal is not None:
        risk_base = risk_base.filter(seal)
    out.at_risk = risk_base.filter(SdTicket.resolution_due_at <= now + timedelta(hours=2)).count()
    out.imminent = risk_base.filter(SdTicket.resolution_due_at <= now + timedelta(minutes=30)).count()
    # Recovery ledger — late tickets the team still brought home today (escape burns).
    rec = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.merged_into_id.is_(None),
        SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= sod,
        SdTicket.resolution_due_at.isnot(None),
        SdTicket.resolved_at > SdTicket.resolution_due_at)
    if seal is not None:
        rec = rec.filter(seal)
    out.recovered_today = rec.count()
    rec_avg = rec.with_entities(func.avg(func.extract(
        "epoch", SdTicket.resolved_at - SdTicket.resolution_due_at))).scalar()
    out.recovered_today_avg_late_minutes = (round(float(rec_avg) / 60.0, 1)
                                            if rec_avg is not None else None)
    # Recovery roster — who is carrying the overdue load (breaching = both clocks missed).
    sq = (base.filter(SdTicket.assigned_agent_id.isnot(None))
          .with_entities(
              SdTicket.assigned_agent_id, func.count(SdTicket.id),
              func.sum(case((and_(res_over, resp_over), 1), else_=0)),
              func.sum(case((SdTicket.priority == TicketPriority.CRITICAL.value, 1), else_=0)))
          .group_by(SdTicket.assigned_agent_id)
          .order_by(func.count(SdTicket.id).desc()).limit(16).all())
    names = _user_names(db, [r[0] for r in sq]) if sq else {}
    out.squad = [SquadLoad(agent_id=aid, name=names.get(str(aid)),
                           open=int(o or 0), breaching=int(b or 0), critical=int(cr or 0))
                 for aid, o, b, cr in sq]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/reopened/stats", response_model=ReopenedStats)
def reopened_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Möbius-Loop aggregate for the Reopened desk — same team seal as the command-center
    list (superuser = whole desk). 'Reopened' is a lifetime marker (reopened_count > 0),
    NOT a status: ACTIVE rows are riding the loop right now; RE-RESOLVED rows made it off
    again. No sweep needed — reopen state is event-driven. Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    seal = None if is_su else _command_center_filter(user, ctx)
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        seal = (SdTicket.assigned_agent_id == user.id)

    out = ReopenedStats()
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d30 = now - timedelta(days=30)

    base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.merged_into_id.is_(None),
        SdTicket.reopened_count > 0)
    if seal is not None:
        base = base.filter(seal)
    active = base.filter(SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))

    out.total_reopened = base.count()
    out.active_reopened = active.count()
    out.re_resolved = base.filter(SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES))).count()
    out.re_resolved_today = base.filter(SdTicket.resolved_at.isnot(None),
                                        SdTicket.resolved_at >= sod).count()
    out.chronic = base.filter(SdTicket.reopened_count >= CHRONIC_REOPEN_THRESHOLD).count()
    out.chronic_open = active.filter(SdTicket.reopened_count >= CHRONIC_REOPEN_THRESHOLD).count()
    out.unassigned_reopened = active.filter(SdTicket.assigned_agent_id.is_(None)).count()
    out.critical_reopened = active.filter(SdTicket.priority == TicketPriority.CRITICAL.value).count()
    # Re-breached = back on the desk AND missing its FRESH re-resolution deadline again.
    out.re_breached = active.filter(SdTicket.sla_resolution_breached == True).count()  # noqa: E712
    out.due_soon_reopened = active.filter(
        SdTicket.sla_paused_since.is_(None),
        SdTicket.sla_resolution_breached == False,  # noqa: E712
        SdTicket.resolution_due_at.isnot(None),
        SdTicket.resolution_due_at > now,
        SdTicket.resolution_due_at <= now + timedelta(hours=2)).count()

    out.by_source = {(s or "unrecorded"): int(c or 0) for s, c in
                     base.with_entities(SdTicket.reopen_source, func.count(SdTicket.id))
                     .group_by(SdTicket.reopen_source).all()}
    out.by_reason = {(r or "uncoded"): int(c or 0) for r, c in
                     base.with_entities(SdTicket.reopen_reason_code, func.count(SdTicket.id))
                     .group_by(SdTicket.reopen_reason_code).all()}
    out.by_priority = {(p or "unset"): int(c or 0) for p, c in
                       base.with_entities(SdTicket.priority, func.count(SdTicket.id))
                       .group_by(SdTicket.priority).all()}
    out.by_status = {(s or "unset"): int(c or 0) for s, c in
                     base.with_entities(SdTicket.status, func.count(SdTicket.id))
                     .group_by(SdTicket.status).all()}

    # The worst offender (most cycles) — the loop's stuck rider.
    worst = base.order_by(SdTicket.reopened_count.desc(), SdTicket.updated_at.desc()).first()
    if worst is not None:
        out.max_reopens = int(worst.reopened_count or 0)
        out.worst = ReopenedWorst(
            ticket_id=worst.id, ticket_number=worst.ticket_number, subject=worst.subject,
            priority=worst.priority, reopened_count=int(worst.reopened_count or 0),
            last_reopened_at=worst.last_reopened_at)

    # 30-day loop physics. reopen_rate denominator = resolves still standing in the window
    # + the reopens themselves (each reopen implies a prior resolve whose stamp was cleared
    # for the fresh cycle) — the honest Zendesk-style "reopens per solve" ratio.
    recent = base.filter(SdTicket.last_reopened_at.isnot(None),
                         SdTicket.last_reopened_at >= d30)
    out.reopens_30d = recent.count()
    lat = (recent.filter(SdTicket.reopen_latency_ms.isnot(None))
           .with_entities(func.avg(SdTicket.reopen_latency_ms),
                          func.max(SdTicket.reopen_latency_ms)).first())
    if lat and lat[0] is not None:
        out.avg_time_to_reopen_minutes = round(float(lat[0]) / 60000.0, 1)
        out.max_time_to_reopen_minutes = round(float(lat[1]) / 60000.0, 1)
    res_q = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.merged_into_id.is_(None),
        SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30)
    if seal is not None:
        res_q = res_q.filter(seal)
    out.resolved_30d = res_q.count()
    solves = out.resolved_30d + out.reopens_30d
    out.reopen_rate_30d = round(100.0 * out.reopens_30d / solves, 1) if solves else 0.0

    # Cycle age — how long the active riders have been back on the desk.
    age = (active.filter(SdTicket.last_reopened_at.isnot(None))
           .with_entities(func.avg(func.extract(
               "epoch", sa_literal(now) - SdTicket.last_reopened_at))).scalar())
    out.avg_cycle_age_minutes = (round(max(0.0, float(age)) / 60.0, 1)
                                 if age is not None else None)

    # Squad — who is carrying the active reopened load (breaching = re-breached).
    sq = (active.filter(SdTicket.assigned_agent_id.isnot(None))
          .with_entities(
              SdTicket.assigned_agent_id, func.count(SdTicket.id),
              func.sum(case((SdTicket.sla_resolution_breached == True, 1), else_=0)),  # noqa: E712
              func.sum(case((SdTicket.priority == TicketPriority.CRITICAL.value, 1), else_=0)))
          .group_by(SdTicket.assigned_agent_id)
          .order_by(func.count(SdTicket.id).desc()).limit(16).all())
    names = _user_names(db, [r[0] for r in sq]) if sq else {}
    out.squad = [SquadLoad(agent_id=aid, name=names.get(str(aid)),
                           open=int(o or 0), breaching=int(b or 0), critical=int(cr or 0))
                 for aid, o, b, cr in sq]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/resolved/stats", response_model=ResolvedStats)
def resolved_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Closeout aggregate for the Resolved desk — same team seal as the command-center
    list (superuser = whole desk), merged tombstones excluded. Runs the auto-close sweep
    first so the pre-close shelf is honest. Two populations: the SHELF (status=resolved,
    inside the 3-day auto-close/reopen window) and the 30-day SURVIVING resolution record
    (a reopen clears resolved_at — bounced fixes are counted via reopens_30d, exactly the
    Reopened desk's denominator). Declared before /{ticket_id} (literal-first)."""
    try:
        from app.routers.support_desk._common import auto_close_due_tickets
        auto_close_due_tickets(db)
    except Exception:
        db.rollback()

    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    seal = None if is_su else _command_center_filter(user, ctx)
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        seal = (SdTicket.assigned_agent_id == user.id)

    out = ResolvedStats()
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d7 = now - timedelta(days=7)
    d14 = now - timedelta(days=14)
    d30 = now - timedelta(days=30)
    window = timedelta(days=SUPPORT_RESOLVED_AUTOCLOSE_DAYS)

    live = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.merged_into_id.is_(None))
    if seal is not None:
        live = live.filter(seal)

    # ── the shelf (status=resolved, pre-close) — one conditional-sum pass ──
    shelf = live.filter(SdTicket.status == TicketStatus.RESOLVED.value)
    row = shelf.with_entities(
        func.count(SdTicket.id),
        func.sum(case((SdTicket.resolved_at < now - window, 1), else_=0)),
        func.sum(case((and_(SdTicket.resolved_at >= now - window,
                            SdTicket.resolved_at < now - (window - timedelta(hours=24))), 1), else_=0)),
        func.min(SdTicket.resolved_at),
        func.sum(case((SdTicket.csat_score.is_(None), 1), else_=0)),
    ).first()
    out.resolved_now = out.pending_close = int(row[0] or 0)
    out.overdue_close = int(row[1] or 0)
    out.due_close_24h = int(row[2] or 0)
    if row[3] is not None:
        out.soonest_auto_close_at = sla_util._aware(row[3]) + window
    out.unrated_shelf = int(row[4] or 0)
    out.closed_total = live.filter(SdTicket.status == TicketStatus.CLOSED.value).count()

    # ── 30-day surviving resolution record ──
    res30 = live.filter(SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d30)
    row = res30.with_entities(
        func.count(SdTicket.id),
        func.sum(case((SdTicket.resolved_at >= sod, 1), else_=0)),
        func.sum(case((SdTicket.resolved_at >= d7, 1), else_=0)),
        func.sum(case((SdTicket.sla_resolution_breached == False, 1), else_=0)),  # noqa: E712
    ).first()
    out.resolved_30d = out.survived_30d = int(row[0] or 0)
    out.resolved_today = int(row[1] or 0)
    out.resolved_7d = int(row[2] or 0)
    out.sla_met_30d = int(row[3] or 0)
    out.sla_met_pct_30d = (round(100.0 * out.sla_met_30d / out.resolved_30d, 1)
                           if out.resolved_30d else None)

    # ── 14-day trend: resolved vs reopened per day (date_trunc, zero-filled in Python) ──
    rday = func.date_trunc("day", SdTicket.resolved_at)
    res_rows = {k: int(v or 0) for k, v in
                live.filter(SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d14)
                .with_entities(rday, func.count(SdTicket.id)).group_by(rday).all()}
    oday = func.date_trunc("day", SdTicket.last_reopened_at)
    rop_rows = {k: int(v or 0) for k, v in
                live.filter(SdTicket.last_reopened_at.isnot(None), SdTicket.last_reopened_at >= d14)
                .with_entities(oday, func.count(SdTicket.id)).group_by(oday).all()}

    def _on(day, rows):
        return next((v for k, v in rows.items() if k is not None and k.date() == day.date()), 0)

    out.trend = [ResolutionTrendBucket(day=day, resolved=_on(day, res_rows),
                                       reopened=_on(day, rop_rows))
                 for day in (sod - timedelta(days=i) for i in range(13, -1, -1))]

    # ── speed: pause-credited time-to-resolve, avg + p50/p90 (percentile_cont) ──
    # NOTE: for bounced tickets created_at is the ORIGINAL open, so TTR spans prior
    # cycles — consistent with how resolved_at works after a reopen.
    ttr_min = (func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)
               - SdTicket.sla_paused_ms / 1000.0) / 60.0
    row = res30.with_entities(
        func.avg(ttr_min),
        func.percentile_cont(0.5).within_group(ttr_min.asc()),
        func.percentile_cont(0.9).within_group(ttr_min.asc()),
        func.avg(SdTicket.time_spent_minutes),
    ).first()
    if row and row[0] is not None:
        out.mttr_avg_minutes = round(max(0.0, float(row[0])), 1)
        out.mttr_p50_minutes = round(max(0.0, float(row[1])), 1) if row[1] is not None else None
        out.mttr_p90_minutes = round(max(0.0, float(row[2])), 1) if row[2] is not None else None
    if row and row[3] is not None:
        out.avg_time_spent_minutes = round(float(row[3]), 1)
    for p, v in (res30.with_entities(SdTicket.priority, func.avg(ttr_min))
                 .group_by(SdTicket.priority).all()):
        if v is not None:
            out.mttr_by_priority[p or "unset"] = round(max(0.0, float(v)), 1)

    # ── FCR (one-touch): never reopened AND <=1 public staff reply (a clean resolve
    # carries 0-1 — the resolution reply itself). One grouped LEFT JOIN, no N+1. ──
    touches = (db.query(SdTicketComment.ticket_id.label("tid"),
                        func.count(SdTicketComment.id).label("n"))
               .filter(SdTicketComment.is_internal == False,  # noqa: E712
                       SdTicketComment.author_kind == CommentAuthorKind.STAFF.value)
               .group_by(SdTicketComment.ticket_id).subquery())
    out.fcr_30d = (res30.outerjoin(touches, touches.c.tid == SdTicket.id)
                   .filter(SdTicket.reopened_count == 0,
                           func.coalesce(touches.c.n, 0) <= 1).count())
    out.fcr_30d_pct = (round(100.0 * out.fcr_30d / out.resolved_30d, 1)
                       if out.resolved_30d else None)

    # ── reopen physics (identical denominator to the Reopened desk) ──
    out.reopens_30d = live.filter(SdTicket.last_reopened_at.isnot(None),
                                  SdTicket.last_reopened_at >= d30).count()
    solves = out.survived_30d + out.reopens_30d
    out.reopen_rate_30d = round(100.0 * out.reopens_30d / solves, 1) if solves else 0.0

    # ── CSAT over the 30d record ──
    rated = res30.filter(SdTicket.csat_score.isnot(None))
    row = rated.with_entities(func.avg(SdTicket.csat_score), func.count(SdTicket.id),
                              func.sum(case((SdTicket.csat_score <= 2, 1), else_=0))).first()
    if row and row[1]:
        out.csat_avg = round(float(row[0]), 2)
        out.csat_count = int(row[1] or 0)
        out.csat_low = int(row[2] or 0)
    out.csat_coverage_pct = (round(100.0 * out.csat_count / out.resolved_30d, 1)
                             if out.resolved_30d else None)
    out.csat_dist = {str(s): int(c or 0) for s, c in
                     rated.with_entities(SdTicket.csat_score, func.count(SdTicket.id))
                     .group_by(SdTicket.csat_score).all()}

    # ── composition (30d) ──
    out.by_resolution_code = {(cde or "uncoded"): int(c or 0) for cde, c in
                              res30.with_entities(SdTicket.resolution_code, func.count(SdTicket.id))
                              .group_by(SdTicket.resolution_code).all()}
    out.by_root_cause = {(rc or "uncategorized"): int(c or 0) for rc, c in
                         res30.with_entities(SdTicket.resolution_category, func.count(SdTicket.id))
                         .group_by(SdTicket.resolution_category).all()}
    out.by_priority = {(p or "unset"): int(c or 0) for p, c in
                       res30.with_entities(SdTicket.priority, func.count(SdTicket.id))
                       .group_by(SdTicket.priority).all()}

    # ── resolver leaderboard (30d; legacy rows fall back to the assignee) ──
    resolver = func.coalesce(SdTicket.resolved_by_id, SdTicket.assigned_agent_id)
    lb = (res30.filter(resolver.isnot(None))
          .with_entities(resolver, func.count(SdTicket.id), func.avg(SdTicket.csat_score),
                         func.avg(ttr_min),
                         func.sum(case((SdTicket.csat_score <= 2, 1), else_=0)))
          .group_by(resolver)
          .order_by(func.count(SdTicket.id).desc()).limit(8).all())
    lb_names = _user_names(db, [r[0] for r in lb]) if lb else {}
    out.leaderboard = [ResolverLoad(
        agent_id=aid, name=lb_names.get(str(aid)), resolved_30d=int(n or 0),
        csat_avg=round(float(cs), 2) if cs is not None else None,
        avg_ttr_minutes=round(max(0.0, float(tv)), 1) if tv is not None else None,
        low_csat=int(lc or 0)) for aid, n, cs, tv, lc in lb]

    # ── squad = who owns the live shelf (breaching = past the close window) ──
    sq = (shelf.filter(SdTicket.assigned_agent_id.isnot(None))
          .with_entities(SdTicket.assigned_agent_id, func.count(SdTicket.id),
                         func.sum(case((SdTicket.resolved_at < now - window, 1), else_=0)),
                         func.sum(case((SdTicket.csat_score <= 2, 1), else_=0)))
          .group_by(SdTicket.assigned_agent_id)
          .order_by(func.count(SdTicket.id).desc()).limit(16).all())
    sq_names = _user_names(db, [r[0] for r in sq]) if sq else {}
    out.squad = [SquadLoad(agent_id=aid, name=sq_names.get(str(aid)),
                           open=int(o or 0), breaching=int(b or 0), critical=int(cr or 0))
                 for aid, o, b, cr in sq]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/closed/stats", response_model=ClosedStats)
def closed_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Archive-of-record aggregate for the Closed desk — same team seal as the
    command-center list (superuser = whole desk). Runs the auto-close sweep first so
    resolved tickets past their window are already in the archive when it's counted.
    Unlike resolved_stats, merged tombstones ARE population here (a merge is a real
    closure record); they're excluded only from the quality math (CSAT / lifespan /
    leaderboard) so duplicates don't distort it. Declared before /{ticket_id}."""
    try:
        from app.routers.support_desk._common import auto_close_due_tickets, auto_archive_old_closed
        auto_close_due_tickets(db)
        auto_archive_old_closed(db)
    except Exception:
        db.rollback()

    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    seal = None if is_su else _command_center_filter(user, ctx)
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        seal = (SdTicket.assigned_agent_id == user.id)

    out = ClosedStats()
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d7 = now - timedelta(days=7)
    d30 = now - timedelta(days=30)
    d365 = now - timedelta(days=365)

    # Base widened for the retention sweep: auto_retention tombstones stay part of the
    # Closed desk's LIFETIME record (counters/trend/kb totals) even after they age out of
    # the closed LIST into Deep Storage — otherwise the sweep would drain this desk.
    # Manually-archived records (spam, duplicates, ...) stay excluded: those were removed
    # from the record on purpose.
    live = db.query(SdTicket).filter(
        or_(SdTicket.is_deleted == False,  # noqa: E712
            and_(SdTicket.is_deleted == True,  # noqa: E712
                 SdTicket.archive_reason_code == ArchiveReason.AUTO_RETENTION.value)))
    if seal is not None:
        live = live.filter(seal)
    closed = live.filter(SdTicket.status == TicketStatus.CLOSED.value)

    # ── volume — one conditional-sum pass on closed_at (NULL-safe: legacy rows sealed
    # before the stamp existed fall only into the lifetime total) ──
    row = closed.with_entities(
        func.count(SdTicket.id),
        func.sum(case((SdTicket.closed_at >= sod, 1), else_=0)),
        func.sum(case((SdTicket.closed_at >= d7, 1), else_=0)),
        func.sum(case((SdTicket.closed_at >= d30, 1), else_=0)),
        func.sum(case((SdTicket.merged_into_id.isnot(None), 1), else_=0)),
    ).first()
    out.closed_total = int(row[0] or 0)
    out.closed_today = int(row[1] or 0)
    out.closed_7d = int(row[2] or 0)
    out.closed_30d = int(row[3] or 0)
    out.merged_total = int(row[4] or 0)
    out.resolved_waiting = live.filter(SdTicket.status == TicketStatus.RESOLVED.value).count()

    # ── closure-source split (30d): HOW each record got sealed. Buckets are mutually
    # exclusive, priority order merged > withdrawn > no_response > auto_sweep > manual,
    # so they always sum to closed_30d. ──
    c30 = closed.filter(SdTicket.closed_at.isnot(None), SdTicket.closed_at >= d30)
    is_merged = SdTicket.merged_into_id.isnot(None)
    not_merged = SdTicket.merged_into_id.is_(None)
    is_withdrawn = and_(not_merged, SdTicket.resolution_code == ResolutionCode.CANCELLED.value)
    is_no_resp = and_(not_merged, SdTicket.resolution_code == ResolutionCode.NO_RESPONSE.value)
    plain = and_(not_merged,
                 or_(SdTicket.resolution_code.is_(None),
                     SdTicket.resolution_code.notin_((ResolutionCode.CANCELLED.value,
                                                      ResolutionCode.NO_RESPONSE.value))))
    row = c30.with_entities(
        func.sum(case((is_merged, 1), else_=0)),
        func.sum(case((is_withdrawn, 1), else_=0)),
        func.sum(case((is_no_resp, 1), else_=0)),
        func.sum(case((and_(plain, SdTicket.closed_by_id.is_(None)), 1), else_=0)),
        func.sum(case((and_(plain, SdTicket.closed_by_id.isnot(None)), 1), else_=0)),
    ).first()
    out.by_close_source = {
        "merged": int(row[0] or 0), "withdrawn": int(row[1] or 0),
        "no_response": int(row[2] or 0), "auto_sweep": int(row[3] or 0),
        "manual": int(row[4] or 0),
    }
    out.auto_closed_30d = out.by_close_source["auto_sweep"]

    # ── composition (30d, whole archive census — merges show up as "uncoded") ──
    out.by_resolution_code = {(cde or "uncoded"): int(c or 0) for cde, c in
                              c30.with_entities(SdTicket.resolution_code, func.count(SdTicket.id))
                              .group_by(SdTicket.resolution_code).all()}
    out.uncoded_30d = out.by_resolution_code.get("uncoded", 0)
    out.by_root_cause = {(rc or "uncategorized"): int(c or 0) for rc, c in
                         c30.with_entities(SdTicket.resolution_category, func.count(SdTicket.id))
                         .group_by(SdTicket.resolution_category).all()}
    out.by_priority = {(p or "unset"): int(c or 0) for p, c in
                       c30.with_entities(SdTicket.priority, func.count(SdTicket.id))
                       .group_by(SdTicket.priority).all()}

    # ── full lifespan created→closed, pause-credited (real records only — a duplicate's
    # lifespan is the master's story, not its own) ──
    real30 = c30.filter(not_merged)
    life_min = (func.extract("epoch", SdTicket.closed_at - SdTicket.created_at)
                - SdTicket.sla_paused_ms / 1000.0) / 60.0
    row = real30.with_entities(
        func.avg(life_min),
        func.percentile_cont(0.5).within_group(life_min.asc()),
        func.percentile_cont(0.9).within_group(life_min.asc()),
    ).first()
    if row and row[0] is not None:
        out.lifespan_avg_minutes = round(max(0.0, float(row[0])), 1)
        out.lifespan_p50_minutes = round(max(0.0, float(row[1])), 1) if row[1] is not None else None
        out.lifespan_p90_minutes = round(max(0.0, float(row[2])), 1) if row[2] is not None else None
    for p, v in (real30.with_entities(SdTicket.priority, func.avg(life_min))
                 .group_by(SdTicket.priority).all()):
        if v is not None:
            out.lifespan_by_priority[p or "unset"] = round(max(0.0, float(v)), 1)

    # ── permanence: exhumed records (agent reopen FROM closed — apply_reopen stamps
    # detail.from with the terminal status being left) ──
    exh = (db.query(func.count(SdTicketActivity.id))
           .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
           .filter(SdTicketActivity.action == "reopened",
                   SdTicketActivity.detail["from"].astext == TicketStatus.CLOSED.value,
                   SdTicketActivity.created_at >= d30,
                   # same widened base as `live` — an exhume whose record later aged into
                   # deep storage still happened
                   or_(SdTicket.is_deleted == False,  # noqa: E712
                       and_(SdTicket.is_deleted == True,  # noqa: E712
                            SdTicket.archive_reason_code == ArchiveReason.AUTO_RETENTION.value))))
    if seal is not None:
        exh = exh.filter(seal)
    out.reopened_from_closed_30d = int(exh.scalar() or 0)
    sealed_total = out.closed_30d + out.reopened_from_closed_30d
    out.closure_survival_pct_30d = (round(100.0 * out.closed_30d / sealed_total, 1)
                                    if sealed_total else None)

    # ── CSAT of record (csat survives close — it's the customer's verdict) ──
    rated = real30.filter(SdTicket.csat_score.isnot(None))
    row = rated.with_entities(func.avg(SdTicket.csat_score), func.count(SdTicket.id),
                              func.sum(case((SdTicket.csat_score <= 2, 1), else_=0))).first()
    if row and row[1]:
        out.csat_avg = round(float(row[0]), 2)
        out.csat_count = int(row[1] or 0)
        out.csat_low = int(row[2] or 0)
    real30_n = real30.count()
    out.csat_coverage_pct = (round(100.0 * out.csat_count / real30_n, 1)
                             if real30_n else None)
    out.csat_dist = {str(s): int(c or 0) for s, c in
                     rated.with_entities(SdTicket.csat_score, func.count(SdTicket.id))
                     .group_by(SdTicket.csat_score).all()}

    # ── knowledge & follow-through (KCS: which sealed fixes deserve an article) ──
    _kb_codes = (ResolutionCode.SOLVED.value, ResolutionCode.WORKAROUND.value,
                 ResolutionCode.KNOWN_ERROR.value, ResolutionCode.CONFIGURATION.value)
    has_kb = SdTicket.links.has_key("kb_article_id")  # noqa: W601 — JSONB ? operator
    out.kb_candidates_30d = real30.filter(
        SdTicket.resolution_code.in_(_kb_codes),
        SdTicket.resolution_summary.isnot(None),
        func.length(func.trim(SdTicket.resolution_summary)) >= 3,
        or_(SdTicket.links.is_(None), ~has_kb),
    ).count()
    out.kb_promoted_total = closed.filter(has_kb).count()
    fu = live.filter(SdTicket.follow_up_of_id.isnot(None))
    out.follow_ups_30d = fu.filter(SdTicket.created_at >= d30).count()
    out.open_follow_ups = fu.filter(
        SdTicket.status.notin_(tuple(TERMINAL_TICKET_STATUSES))).count()

    # ── the chronicle: 12 monthly closure cohorts (date_trunc, zero-filled) ──
    cmonth = func.date_trunc("month", SdTicket.closed_at)
    mrows = {k: int(v or 0) for k, v in
             closed.filter(SdTicket.closed_at.isnot(None), SdTicket.closed_at >= d365)
             .with_entities(cmonth, func.count(SdTicket.id)).group_by(cmonth).all()}

    def _in_month(mo):
        return next((v for k, v in mrows.items()
                     if k is not None and k.year == mo.year and k.month == mo.month), 0)

    som = sod.replace(day=1)
    months, y, m = [], som.year, som.month
    for _ in range(12):
        months.append(som.replace(year=y, month=m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    out.trend = [ClosureTrendBucket(month=mo, closed=_in_month(mo)) for mo in reversed(months)]

    # ── top closers (30d; merge/legacy rows fall back to resolver → assignee) ──
    closer = func.coalesce(SdTicket.closed_by_id, SdTicket.resolved_by_id,
                           SdTicket.assigned_agent_id)
    lb = (real30.filter(closer.isnot(None))
          .with_entities(closer, func.count(SdTicket.id), func.avg(SdTicket.csat_score),
                         func.avg(life_min))
          .group_by(closer)
          .order_by(func.count(SdTicket.id).desc()).limit(8).all())
    lb_names = _user_names(db, [r[0] for r in lb]) if lb else {}
    out.leaderboard = [CloserLoad(
        agent_id=aid, name=lb_names.get(str(aid)), closed_30d=int(n or 0),
        csat_avg=round(float(cs), 2) if cs is not None else None,
        avg_lifespan_minutes=round(max(0.0, float(lv)), 1) if lv is not None else None)
        for aid, n, cs, lv in lb]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


@router.get("/archived/stats", response_model=ArchivedStats)
def archived_stats(mine: bool = Query(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Deep-storage aggregate for the Archived desk — same team seal as the
    command-center list (superuser = whole desk). Runs the retention sweep first so
    closed records past the auto-archive window are already on the shelf when it's
    counted. Population = tombstones (is_deleted=True). Declared before /{ticket_id}."""
    try:
        from app.routers.support_desk._common import auto_archive_old_closed
        auto_archive_old_closed(db)
    except Exception:
        db.rollback()

    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    seal = None if is_su else _command_center_filter(user, ctx)
    if mine:   # personal-desk lens: the user-portal desks aggregate MY assignments only
        seal = (SdTicket.assigned_agent_id == user.id)

    out = ArchivedStats()
    out.retention_days = SUPPORT_ARCHIVE_RETENTION_DAYS
    out.autoarchive_days = SUPPORT_CLOSED_AUTOARCHIVE_DAYS or None
    now = sla_util.now_utc()
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d7 = now - timedelta(days=7)
    d30 = now - timedelta(days=30)
    d90 = now - timedelta(days=90)
    d365 = now - timedelta(days=365)
    purge_cut = now - timedelta(days=SUPPORT_ARCHIVE_RETENTION_DAYS)
    # archived_at ∈ [purge_cut, expiring_cut) ⇒ becomes purge-eligible within the window
    expiring_cut = purge_cut + timedelta(days=SUPPORT_ARCHIVE_EXPIRING_SOON_DAYS)

    arch = db.query(SdTicket).filter(SdTicket.is_deleted == True)  # noqa: E712
    if seal is not None:
        arch = arch.filter(seal)

    not_held = SdTicket.legal_hold == False  # noqa: E712

    # ── volume + governance — one conditional-sum pass on archived_at (NULL-safe:
    # the migration backfilled every tombstone, but a raw-SQL row would only fall
    # into the lifetime total) ──
    row = arch.with_entities(
        func.count(SdTicket.id),
        func.sum(case((SdTicket.archived_at >= sod, 1), else_=0)),
        func.sum(case((SdTicket.archived_at >= d7, 1), else_=0)),
        func.sum(case((SdTicket.archived_at >= d30, 1), else_=0)),
        func.sum(case((SdTicket.legal_hold == True, 1), else_=0)),  # noqa: E712
        func.sum(case((and_(not_held, SdTicket.archived_at.isnot(None),
                            SdTicket.archived_at < purge_cut), 1), else_=0)),
        func.sum(case((and_(not_held, SdTicket.archived_at.isnot(None),
                            SdTicket.archived_at >= purge_cut,
                            SdTicket.archived_at < expiring_cut), 1), else_=0)),
        func.sum(case((and_(SdTicket.archive_reason_code == ArchiveReason.AUTO_RETENTION.value,
                            SdTicket.archived_at >= d30), 1), else_=0)),
        func.min(SdTicket.archived_at),
    ).first()
    out.total_archived = int(row[0] or 0)
    out.archived_today = int(row[1] or 0)
    out.archived_7d = int(row[2] or 0)
    out.archived_30d = int(row[3] or 0)
    out.legal_hold_count = int(row[4] or 0)
    out.purge_eligible_count = int(row[5] or 0)
    out.expiring_soon_count = int(row[6] or 0)
    out.auto_archived_30d = int(row[7] or 0)
    out.oldest_archived_at = row[8]

    # ── composition (whole shelf census — the archive is small by nature) ──
    out.by_reason_code = {(rc or "uncoded"): int(c or 0) for rc, c in
                          arch.with_entities(SdTicket.archive_reason_code, func.count(SdTicket.id))
                          .group_by(SdTicket.archive_reason_code).all()}
    out.uncoded = out.by_reason_code.get("uncoded", 0)
    # DELETE preserves status, so the shelf remembers what each record WAS.
    out.by_status_at_archive = {(s or "unknown"): int(c or 0) for s, c in
                                arch.with_entities(SdTicket.status, func.count(SdTicket.id))
                                .group_by(SdTicket.status).all()}
    out.open_at_archive = (out.by_status_at_archive.get(TicketStatus.OPEN.value, 0)
                           + out.by_status_at_archive.get(TicketStatus.IN_PROGRESS.value, 0))
    out.by_priority = {(p or "unset"): int(c or 0) for p, c in
                       arch.with_entities(SdTicket.priority, func.count(SdTicket.id))
                       .group_by(SdTicket.priority).all()}

    # ── age strata + dormancy (now − archived_at) ──
    row = arch.filter(SdTicket.archived_at.isnot(None)).with_entities(
        func.sum(case((SdTicket.archived_at >= d7, 1), else_=0)),
        func.sum(case((and_(SdTicket.archived_at < d7, SdTicket.archived_at >= d30), 1), else_=0)),
        func.sum(case((and_(SdTicket.archived_at < d30, SdTicket.archived_at >= d90), 1), else_=0)),
        func.sum(case((SdTicket.archived_at < d90, 1), else_=0)),
    ).first()
    out.age_cohorts = {"lt_7d": int(row[0] or 0), "d7_30": int(row[1] or 0),
                       "d30_90": int(row[2] or 0), "gt_90": int(row[3] or 0)}
    dorm_min = func.extract("epoch", sa_literal(now) - SdTicket.archived_at) / 60.0
    p50 = (arch.filter(SdTicket.archived_at.isnot(None))
           .with_entities(func.percentile_cont(0.5).within_group(dorm_min.asc())).scalar())
    out.dormancy_p50_minutes = round(max(0.0, float(p50)), 1) if p50 is not None else None

    # ── restores (activity-derived; restored rows are LIVE again, so the seal applies
    # through the ticket join, not the tombstone base) ──
    res_base = (db.query(SdTicketActivity)
                .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
                .filter(SdTicketActivity.action == "restored"))
    if seal is not None:
        res_base = res_base.filter(seal)
    out.restored_30d = int(res_base.filter(SdTicketActivity.created_at >= d30)
                           .with_entities(func.count(SdTicketActivity.id)).scalar() or 0)
    out.restored_by_30d = {(n or "System"): int(c or 0) for n, c in
                           res_base.filter(SdTicketActivity.created_at >= d30)
                           .with_entities(SdTicketActivity.actor_name, func.count(SdTicketActivity.id))
                           .group_by(SdTicketActivity.actor_name).all()}

    # ── the chronicle: 12 monthly cohorts, shelved vs pulled back (zero-filled) ──
    amonth = func.date_trunc("month", SdTicket.archived_at)
    arows = {k: int(v or 0) for k, v in
             arch.filter(SdTicket.archived_at.isnot(None), SdTicket.archived_at >= d365)
             .with_entities(amonth, func.count(SdTicket.id)).group_by(amonth).all()}
    rmonth = func.date_trunc("month", SdTicketActivity.created_at)
    rrows = {k: int(v or 0) for k, v in
             res_base.filter(SdTicketActivity.created_at >= d365)
             .with_entities(rmonth, func.count(SdTicketActivity.id)).group_by(rmonth).all()}

    def _in_month(rows, mo):
        return next((v for k, v in rows.items()
                     if k is not None and k.year == mo.year and k.month == mo.month), 0)

    som = sod.replace(day=1)
    months, y, m = [], som.year, som.month
    for _ in range(12):
        months.append(som.replace(year=y, month=m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    out.trend = [ArchiveTrendBucket(month=mo, archived=_in_month(arows, mo),
                                    restored=_in_month(rrows, mo)) for mo in reversed(months)]

    # ── top archivers (manual archives on the current shelf; System/auto rows excluded) ──
    lb = (arch.filter(SdTicket.archived_by_id.isnot(None))
          .with_entities(SdTicket.archived_by_id, func.count(SdTicket.id),
                         func.sum(case((SdTicket.archived_at >= d30, 1), else_=0)))
          .group_by(SdTicket.archived_by_id)
          .order_by(func.count(SdTicket.id).desc()).limit(8).all())
    lb_names = _user_names(db, [r[0] for r in lb]) if lb else {}
    out.top_archivers = [ArchiverLoad(
        agent_id=aid, name=lb_names.get(str(aid)),
        archived_total=int(n or 0), archived_30d=int(n30 or 0))
        for aid, n, n30 in lb]
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    return out


# ─────────────────── Unassigned intake queue (Claim Field) ───────────────────
def _unassigned_pool(db: Session, user: User, ctx: dict, is_su: bool):
    """Base query for the 'claimable pool': open + unowned, team-sealed for agents.
    A superuser gets the whole desk. Shared by the list / stats / claim-next routes."""
    q = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.assigned_agent_id.is_(None),
        SdTicket.status.in_(OPEN_TICKET_STATUSES),
    )
    if not is_su:
        q = q.filter(_team_queue_filter(ctx))
    return q


@router.get("/unassigned-queue", response_model=TicketListResponse)
def list_unassigned_queue(
    lane: str = Query("all"),
    q: Optional[str] = None,
    priority: Optional[str] = None,
    ticket_type: Optional[str] = None,
    team_id: Optional[UUID] = None,
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("asc"),
    page: int = Query(1, ge=1),
    limit: int = Query(150, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The Unassigned intake queue — open, unowned tickets in the caller's claimable pool
    (their teams + the triage pool routing to their teams). Team-sealed for non-superusers
    (a superuser sees the whole desk). `lane` = all|team|triage narrows to already-routed
    vs untriaged. Declared before /{ticket_id} (literal-first)."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    query = _unassigned_pool(db, user, ctx, is_su)
    if lane == "team":
        query = query.filter(SdTicket.team_id.isnot(None))
    elif lane == "triage":
        query = query.filter(SdTicket.team_id.is_(None))
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if ticket_type:
        query = query.filter(SdTicket.ticket_type == ticket_type)
    if team_id and (is_su or team_id in ctx["team_ids"]):
        query = query.filter(SdTicket.team_id == team_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))
    total = query.count()
    col = _SELF_SORT.get(sort_by, SdTicket.created_at)
    col = col.asc() if (sort_dir or "asc").lower() == "asc" else col.desc()
    items = query.order_by(col, SdTicket.created_at.asc()).offset((page - 1) * limit).limit(limit).all()
    enrich_tickets(db, items)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in items],
        total=total, page=page, limit=limit,
    )


@router.get("/unassigned-queue/stats", response_model=UnassignedQueueStats)
def unassigned_queue_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Aggregate for the Unassigned queue — same scope as the list, so the lens counts
    reconcile. Declared before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    base = _unassigned_pool(db, user, ctx, is_su)
    out = UnassignedQueueStats()
    out.total = base.count()
    out.team_queue = base.filter(SdTicket.team_id.isnot(None)).count()
    out.triage_pool = base.filter(SdTicket.team_id.is_(None)).count()
    out.by_priority = {p: 0 for p in PRIORITY_ORDER}
    for pr, cnt in (base.with_entities(SdTicket.priority, func.count(SdTicket.id))
                    .group_by(SdTicket.priority).all()):
        if pr in out.by_priority:
            out.by_priority[pr] = int(cnt or 0)
    # Per-team slice of the already-routed lane (drives the lane picker).
    trows = (base.filter(SdTicket.team_id.isnot(None))
             .with_entities(SdTicket.team_id, func.count(SdTicket.id))
             .group_by(SdTicket.team_id).all())
    tnames = {}
    if trows:
        for tid, tname in (db.query(SdTeam.id, SdTeam.name)
                           .filter(SdTeam.id.in_([r[0] for r in trows])).all()):
            tnames[tid] = tname
    out.teams = [UnassignedQueueTeam(team_id=tid, name=tnames.get(tid), count=int(c or 0)) for tid, c in trows]
    out.teams.sort(key=lambda t: -t.count)
    # SLA breach / due-soon / oldest — load rows (cap 800) and reuse the same SLA helper as the UI.
    now = sla_util.now_utc()
    rows = base.order_by(SdTicket.created_at.asc()).limit(800).all()
    breaching = due_soon = 0
    for t in rows:
        rs = sla_util.resolution_state(t, now)
        if t.sla_resolution_breached or rs == "breached":
            breaching += 1
        elif rs == "due-soon":
            due_soon += 1
    out.breaching = breaching
    out.due_soon = due_soon
    if rows:
        oldest = sla_util._aware(rows[0].created_at)
        out.oldest_age_minutes = int((now - oldest).total_seconds() // 60) if oldest else 0
    return out


@router.post("/claim-next", response_model=TicketResponse)
def claim_next(
    payload: ClaimNext,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Guided-mode claim: atomically assign the single highest-priority unowned ticket in
    the caller's claimable pool to the caller (stamping its team if untriaged), so two
    agents pressing 'Claim Next' never grab the same ticket. Ranks breached-first, then
    soonest-due, then priority, then oldest. 404 when the queue is empty. Before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    if not (_is_agent(user) or ctx["teams"]):
        raise HTTPException(403, "Only a support agent or a member of a support team can claim tickets.")
    query = _unassigned_pool(db, user, ctx, is_su)
    if payload.lane == "team":
        query = query.filter(SdTicket.team_id.isnot(None))
    elif payload.lane == "triage":
        query = query.filter(SdTicket.team_id.is_(None))
    if payload.team_id and (is_su or payload.team_id in ctx["team_ids"]):
        query = query.filter(SdTicket.team_id == payload.team_id)
    cands = query.limit(200).all()
    if not cands:
        raise HTTPException(404, "No unassigned tickets in your queue to claim.")
    now = sla_util.now_utc()

    def _rank(t):
        breached = bool(t.sla_resolution_breached) or sla_util.resolution_state(t, now) == "breached"
        due = sla_util._aware(t.resolution_due_at)
        due_key = due.timestamp() if due else float("inf")
        try:
            pr = PRIORITY_ORDER.index(t.priority)   # low..critical → 0..4; higher = more urgent
        except ValueError:
            pr = -1
        created = sla_util._aware(t.created_at)
        return (0 if breached else 1, due_key, -pr, created.timestamp() if created else 0.0)

    cands.sort(key=_rank)
    t = cands[0]
    if t.assigned_agent_id is not None:            # defensive re-check (serialized get_db, but be safe)
        raise HTTPException(409, "That ticket was just claimed — try again.")
    prev = t.assigned_agent_id
    t.assigned_agent_id = user.id
    _stamp_team_on_claim(t, ctx, user.id)
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Agent",
                            action="assigned", detail={"assigned_agent_id": str(user.id), "by": "claim-next"}))
    if t.assigned_agent_id != prev:
        _dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                       title=f"Claimed: {t.subject}", action_url="/user/support/tickets/my")
    write_audit(db, entity_type="ticket", op="assigned", entity_id=t.id,
                actor_id=user.id, request=request, details={"assigned_agent_id": str(user.id), "by": "claim-next"})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────── Team Ops desk (agent-side Team Tickets) ───────────────────
def _team_ops_scope(db: Session, ctx: dict, is_su: bool, team_id):
    """Which teams the Team Ops surfaces aggregate over. Non-superusers may only see
    teams they are on (an out-of-scope team_id 403s — never leaks another team's desk);
    a superuser may select any team, or gets every active team with no selection.
    Returns (scope_teams, scoped_team_ids, selected_team_or_None)."""
    if is_su:
        all_teams = (db.query(SdTeam)
                     .filter(SdTeam.is_deleted == False, SdTeam.is_active == True)  # noqa: E712
                     .order_by(SdTeam.name).all())
        if team_id:
            sel = next((tm for tm in all_teams if str(tm.id) == str(team_id)), None)
            if not sel:
                raise HTTPException(404, "Team not found")
            return [sel], [sel.id], sel
        return all_teams, [tm.id for tm in all_teams], (all_teams[0] if len(all_teams) == 1 else None)
    if team_id:
        sel = next((tm for tm in ctx["teams"] if str(tm.id) == str(team_id)), None)
        if not sel:
            raise HTTPException(403, "You are not a member of that support team.")
        return [sel], [sel.id], sel
    teams = ctx["teams"]
    return teams, [tm.id for tm in teams], (teams[0] if len(teams) == 1 else None)


# Extracted to app/utils/support_desk/team_ops.py so the admin Team Command overview
# (/teams/overview) shares the exact same lens math — aliased here so the ~8 existing
# call sites in this file are untouched.
_team_ops_conds = team_ops_conds


def _attach_viewers(db: Session, items: list, me_id=None) -> None:
    """Zendesk-style collision pips: attach the live viewers (last 60s) of each row in
    one grouped query. The caller's own heartbeat is skipped — a pip means SOMEONE ELSE."""
    ids = [t.id for t in items]
    if not ids:
        return
    nowt = sla_util.now_utc()
    live = (db.query(SdTicketViewer.ticket_id, SdTicketViewer.user_id)
            .filter(SdTicketViewer.ticket_id.in_(ids),
                    SdTicketViewer.last_seen_at >= nowt - timedelta(seconds=60)).all())
    by_ticket: dict = {}
    if live:
        names = _user_names(db, [uid for _, uid in live])
        for tid, uid in live:
            if me_id is not None and str(uid) == str(me_id):
                continue
            by_ticket.setdefault(tid, []).append({"user_id": str(uid), "name": names.get(str(uid))})
    for t in items:
        t.viewers = by_ticket.get(t.id, [])


@router.get("/team-queue", response_model=TicketListResponse)
def list_team_queue(
    team_id: Optional[UUID] = None,
    lens: Optional[str] = Query(None, description="all|unassigned|mine|breaching|due_soon|idle|escalated|pending|critical"),
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    ticket_type: Optional[str] = None,
    assigned_agent_id: Optional[UUID] = None,
    q: Optional[str] = None,
    sort_by: str = Query("updated_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The Team Ops queue — tickets ROUTED TO the support team(s) the caller is on
    (member or lead), backend-sealed. This is the working desk: merged tombstones are
    excluded and the default working set is ACTIVE (non-terminal) — terminal records
    live on the Resolved/Closed desks; the untriaged pool lives on the Unassigned desk.
    An out-of-scope team_id 403s; an out-of-scope assigned_agent_id is silently dropped
    (narrow-only, mirroring the command center). Declared before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    scope_teams, scoped_ids, _sel = _team_ops_scope(db, ctx, is_su, team_id)
    if not scoped_ids:
        return TicketListResponse(items=[], total=0, page=page, limit=limit)
    # Hold-expiry sweep: an expired hold re-enters the live queue the moment the desk loads.
    try:
        auto_resume_expired_holds(db)
    except Exception:
        db.rollback()
    now = sla_util.now_utc()
    conds = _team_ops_conds(now)
    query = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.merged_into_id.is_(None),
        SdTicket.team_id.in_(scoped_ids),
    )
    terminal = list(TERMINAL_TICKET_STATUSES)
    if lens == "unassigned":
        query = query.filter(SdTicket.assigned_agent_id.is_(None),
                             SdTicket.status.in_(list(OPEN_TICKET_STATUSES)))
    elif lens == "mine":
        query = query.filter(SdTicket.assigned_agent_id == user.id,
                             SdTicket.status.notin_(terminal))
    elif lens == "breaching":
        query = query.filter(SdTicket.status.notin_(terminal), conds["breach"])
    elif lens == "due_soon":
        query = query.filter(conds["due_soon"])
    elif lens == "idle":
        query = query.filter(conds["idle"])
    elif lens == "escalated":
        query = query.filter(SdTicket.is_escalated == True,  # noqa: E712
                             SdTicket.status.notin_(terminal))
    elif lens == "pending":
        query = query.filter(conds["pending"])
    elif lens == "critical":
        query = query.filter(SdTicket.status.notin_(terminal), conds["critical"])
    else:  # all — the active working set
        query = query.filter(SdTicket.status.notin_(terminal))
    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if ticket_type:
        query = query.filter(SdTicket.ticket_type == ticket_type)
    # Narrow-only: honour an agent filter ONLY within the caller's reach (superuser anywhere).
    if assigned_agent_id and (is_su or assigned_agent_id in (ctx["member_ids"] | {user.id})):
        query = query.filter(SdTicket.assigned_agent_id == assigned_agent_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))
    total = query.count()
    col = _SELF_SORT.get(sort_by, SdTicket.updated_at)
    col = col.asc() if (sort_dir or "desc").lower() == "asc" else col.desc()
    items = query.order_by(col, SdTicket.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    enrich_tickets(db, items)
    _attach_viewers(db, items, me_id=user.id)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in items],
        total=total, page=page, limit=limit,
    )


# Extracted to app/utils/support_desk/team_ops.py (shared with /teams/overview).
_team_on_shift = team_on_shift


@router.get("/team-queue/stats", response_model=TeamQueueStats)
def team_queue_stats(
    team_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Team Ops aggregate — the SAME seal + lens math as /team-queue, so every hero lens
    reconciles with the working set. Runs the idempotent hold/auto-close/breach sweeps
    first so the physics are honest. Declared before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    scope_teams, scoped_ids, sel = _team_ops_scope(db, ctx, is_su, team_id)
    out = TeamQueueStats()
    now = sla_util.now_utc()
    out.generated_at = now
    out.team_count = len(ctx["teams"])
    out.team_names = [tm.name for tm in ctx["teams"]]
    if not scoped_ids:
        return out

    # ── sweeps (idempotent; each guards itself) ──
    try:
        auto_resume_expired_holds(db)
    except Exception:
        db.rollback()
    try:
        from app.routers.support_desk._common import auto_close_due_tickets
        auto_close_due_tickets(db)
    except Exception:
        db.rollback()
    try:
        from app.utils.support_desk.breach import sweep_sla_breach_flags
        if sweep_sla_breach_flags(db, team_cond=SdTicket.team_id.in_(scoped_ids)):
            db.commit()
    except Exception:
        db.rollback()

    conds = _team_ops_conds(now)
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d7, d14 = now - timedelta(days=7), now - timedelta(days=14)
    terminal = list(TERMINAL_TICKET_STATUSES)
    base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.merged_into_id.is_(None),
        SdTicket.team_id.in_(scoped_ids),
    )
    active = base.filter(SdTicket.status.notin_(terminal))

    # ── switcher chips (per-team live load) ──
    switch_ids = [tm.id for tm in (scope_teams if is_su else ctx["teams"])]
    tcounts = {}
    if switch_ids:
        tcounts = {tid: int(c or 0) for tid, c in
                   db.query(SdTicket.team_id, func.count(SdTicket.id))
                   .filter(SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.team_id.in_(switch_ids),
                           SdTicket.status.notin_(terminal))
                   .group_by(SdTicket.team_id).all()}
    uid = str(user.id)
    for tm in (scope_teams if is_su else ctx["teams"])[:50]:
        out.teams.append(TeamSwitcherEntry(
            id=tm.id, name=tm.name, color=tm.color,
            is_lead=_is_lead(tm, user.id),
            member_count=len(tm.member_ids or []),
            open_count=tcounts.get(tm.id, 0)))

    # ── selected-team identity ──
    if sel is not None:
        out.team_id, out.team_name, out.team_color = sel.id, sel.name, sel.color
        out.assignment_method = sel.assignment_method
        out.business_hours = sel.business_hours if isinstance(sel.business_hours, dict) else {}
        out.request_types = [str(x) for x in (sel.request_types or [])]
        out.can_distribute = is_su or _is_lead(sel, user.id)
        if sel.lead_user_id:
            out.lead_name = _user_names(db, [sel.lead_user_id]).get(str(sel.lead_user_id))
    else:
        out.can_distribute = is_su or any(_is_lead(tm, user.id) for tm in scope_teams)

    # ── queue totals — one conditional-sum pass over the active set ──
    row = active.with_entities(
        func.count(SdTicket.id),
        func.sum(case((and_(SdTicket.assigned_agent_id.is_(None),
                            SdTicket.status.in_(list(OPEN_TICKET_STATUSES))), 1), else_=0)),
        func.sum(case((conds["breach"], 1), else_=0)),
        func.sum(case((conds["due_soon"], 1), else_=0)),
        func.sum(case((conds["idle"], 1), else_=0)),
        func.sum(case((SdTicket.is_escalated == True, 1), else_=0)),  # noqa: E712
        func.sum(case((SdTicket.status == TicketStatus.PENDING_CUSTOMER.value, 1), else_=0)),
        func.sum(case((SdTicket.status == TicketStatus.PENDING_VENDOR.value, 1), else_=0)),
        func.sum(case((SdTicket.status == TicketStatus.ON_HOLD.value, 1), else_=0)),
        func.sum(case((conds["critical"], 1), else_=0)),
        func.sum(case((SdTicket.reopened_count > 0, 1), else_=0)),
    ).first()
    (out.queue, out.unassigned, out.breached_active, out.due_4h, out.idle_24h,
     out.escalated, out.pending_customer, out.pending_vendor, out.on_hold,
     out.critical, out.reopened_active) = [int(x or 0) for x in row]
    out.resolved_today = base.filter(SdTicket.resolved_at.isnot(None),
                                     SdTicket.resolved_at >= sod).count()
    out.by_priority = {p: 0 for p in PRIORITY_ORDER}
    for p, c in active.with_entities(SdTicket.priority, func.count(SdTicket.id)).group_by(SdTicket.priority).all():
        if p in out.by_priority:
            out.by_priority[p] = int(c or 0)
    out.by_status = {(s or "unset"): int(c or 0) for s, c in
                     active.with_entities(SdTicket.status, func.count(SdTicket.id))
                     .group_by(SdTicket.status).all()}

    # ── roster (union of the scoped teams' members; lead > agent > collaborator) ──
    rank = {"collaborator": 0, "agent": 1, "lead": 2}
    meta: dict[str, dict] = {}
    for tm in scope_teams:
        roles = tm.member_roles or {}
        lead = str(tm.lead_user_id) if tm.lead_user_id else None
        for m in list(tm.member_ids or []) + ([lead] if lead else []):
            mid = str(m)
            role = "lead" if mid == lead else str(roles.get(mid, "agent"))
            if role not in rank:
                role = "agent"
            prev = meta.get(mid)
            if not prev or rank[role] > rank[prev["role"]]:
                meta[mid] = {"role": role}
    on_shift = _team_on_shift(sel.business_hours, now) if sel is not None else None
    age1, age3, age7 = now - timedelta(days=1), now - timedelta(days=3), now - timedelta(days=7)
    arows = (active.filter(SdTicket.assigned_agent_id.isnot(None))
             .with_entities(
                 SdTicket.assigned_agent_id,
                 func.count(SdTicket.id),
                 func.sum(case((SdTicket.status == TicketStatus.IN_PROGRESS.value, 1), else_=0)),
                 func.sum(case((conds["pending"], 1), else_=0)),
                 func.sum(case((conds["breach"], 1), else_=0)),
                 func.sum(case((conds["critical"], 1), else_=0)),
                 func.sum(case((conds["due_soon"], 1), else_=0)),
                 func.sum(case((conds["idle"], 1), else_=0)),
                 func.sum(case((SdTicket.created_at >= age1, 1), else_=0)),
                 func.sum(case((and_(SdTicket.created_at < age1, SdTicket.created_at >= age3), 1), else_=0)),
                 func.sum(case((and_(SdTicket.created_at < age3, SdTicket.created_at >= age7), 1), else_=0)),
                 func.sum(case((SdTicket.created_at < age7, 1), else_=0)),
             ).group_by(SdTicket.assigned_agent_id).all())
    load = {str(r[0]): r for r in arows}
    resolver = func.coalesce(SdTicket.resolved_by_id, SdTicket.assigned_agent_id)
    res7 = base.filter(SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d7)
    fixed = {str(aid): (int(n or 0), (round(float(cs), 2) if cs is not None else None))
             for aid, n, cs in
             res7.filter(resolver.isnot(None))
             .with_entities(resolver, func.count(SdTicket.id), func.avg(SdTicket.csat_score))
             .group_by(resolver).all()}
    names = _user_names(db, list({*meta.keys(), *load.keys(), *fixed.keys()})) if (meta or load or fixed) else {}
    roster_ids = set(meta.keys()) | (set(load.keys()) if is_su and sel is None else set())
    for mid in roster_ids:
        m = meta.get(mid, {"role": "agent"})
        r = load.get(mid)
        n7, cs = fixed.get(mid, (0, None))
        out.roster.append(TeamRosterEntry(
            agent_id=UUID(mid), name=names.get(mid), role=m["role"],
            is_lead=(m["role"] == "lead"), on_shift=on_shift,
            open=int(r[1] or 0) if r else 0,
            in_progress=int(r[2] or 0) if r else 0,
            pending=int(r[3] or 0) if r else 0,
            breaching=int(r[4] or 0) if r else 0,
            critical=int(r[5] or 0) if r else 0,
            due_soon=int(r[6] or 0) if r else 0,
            idle=int(r[7] or 0) if r else 0,
            aging_1d=int(r[8] or 0) if r else 0,
            aging_3d=int(r[9] or 0) if r else 0,
            aging_7d=int(r[10] or 0) if r else 0,
            aging_7plus=int(r[11] or 0) if r else 0,
            resolved_7d=n7, csat_avg=cs))
    out.roster.sort(key=lambda e: (rank.get(e.role, 1) == 0, -e.open, -e.breaching, (e.name or "").lower()))
    out.roster = out.roster[:32]

    # ── 14-day inflow/outflow balance (date_trunc, zero-filled) ──
    cday = func.date_trunc("day", SdTicket.created_at)
    inflow = {k: int(v or 0) for k, v in
              base.filter(SdTicket.created_at >= d14)
              .with_entities(cday, func.count(SdTicket.id)).group_by(cday).all()}
    rday = func.date_trunc("day", SdTicket.resolved_at)
    outflow = {k: int(v or 0) for k, v in
               base.filter(SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d14)
               .with_entities(rday, func.count(SdTicket.id)).group_by(rday).all()}

    def _on(day, rows):
        return next((v for k, v in rows.items() if k is not None and k.date() == day.date()), 0)

    out.flow = [TeamFlowBucket(day=day, inflow=_on(day, inflow), outflow=_on(day, outflow))
                for day in (sod - timedelta(days=i) for i in range(13, -1, -1))]

    # ── speed (7d, pause-credited) ──
    ttr_min = (func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)
               - SdTicket.sla_paused_ms / 1000.0) / 60.0
    row = res7.with_entities(
        func.percentile_cont(0.5).within_group(ttr_min.asc()),
        func.percentile_cont(0.9).within_group(ttr_min.asc())).first()
    if row and row[0] is not None:
        out.mttr_p50_7d = round(max(0.0, float(row[0])), 1)
        out.mttr_p90_7d = round(max(0.0, float(row[1])), 1) if row[1] is not None else None
    frt_min = func.extract("epoch", SdTicket.first_responded_at - SdTicket.created_at) / 60.0
    frt = (base.filter(SdTicket.first_responded_at.isnot(None), SdTicket.first_responded_at >= d7)
           .with_entities(func.percentile_cont(0.5).within_group(frt_min.asc())).scalar())
    if frt is not None:
        out.frt_p50_7d = round(max(0.0, float(frt)), 1)

    # ── leaderboard (resolved 7d) ──
    out.leaderboard = [TeamLeaderEntry(agent_id=UUID(aid), name=names.get(aid) or _user_names(db, [aid]).get(aid),
                                       resolved_7d=n, csat_avg=cs)
                       for aid, (n, cs) in sorted(fixed.items(), key=lambda kv: -kv[1][0])[:8]]

    # ── collision hotspots (≥2 live viewers on a scoped ticket) ──
    hot = (db.query(SdTicketViewer.ticket_id, func.count(SdTicketViewer.user_id))
           .filter(SdTicketViewer.last_seen_at >= now - timedelta(seconds=60))
           .group_by(SdTicketViewer.ticket_id)
           .having(func.count(SdTicketViewer.user_id) >= 2).all())
    if hot:
        hot_ids = [h[0] for h in hot]
        counts = {h[0]: int(h[1] or 0) for h in hot}
        scoped = base.filter(SdTicket.id.in_(hot_ids)).limit(6).all()
        if scoped:
            vrows = (db.query(SdTicketViewer.ticket_id, SdTicketViewer.user_id)
                     .filter(SdTicketViewer.ticket_id.in_([t.id for t in scoped]),
                             SdTicketViewer.last_seen_at >= now - timedelta(seconds=60)).all())
            vnames = _user_names(db, [u for _, u in vrows])
            per: dict = {}
            for tid, u in vrows:
                per.setdefault(tid, []).append(vnames.get(str(u)) or "Agent")
            out.hotspots = [TeamHotspot(ticket_id=t.id, ticket_number=t.ticket_number,
                                        subject=t.subject, viewer_count=counts.get(t.id, 0),
                                        viewer_names=per.get(t.id, []))
                            for t in scoped]
    return out


@router.post("/team-queue/distribute", response_model=TeamDistributeResult)
def distribute_team_queue(
    payload: TeamDistributeRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Round-robin / load-balanced distribution of the team's unowned queue across its
    workable members (ServiceNow-style rebalance) — team-lead or superuser only. Honours
    the team's assignment_method + rr cursor, spreads the most urgent work first (same
    rank as claim-next), caps per call, and writes one audited 'assigned' activity per
    ticket. Declared before /{ticket_id}."""
    is_su = bool(getattr(user, "is_superuser", False))
    team = db.query(SdTeam).filter(SdTeam.id == payload.team_id,
                                   SdTeam.is_deleted == False,  # noqa: E712
                                   SdTeam.is_active == True).first()  # noqa: E712
    if not team:
        raise HTTPException(404, "Team not found")
    if not (is_su or _is_lead(team, user.id)):
        raise HTTPException(403, "Only the team lead (or an administrator) can distribute the team queue.")
    pool = _agents_of_team(db, team)
    if not pool:
        raise HTTPException(409, "This team has no assignable members.")
    cap = max(1, min(int(payload.max_tickets or 25), 50))
    cands = (db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.merged_into_id.is_(None),
        SdTicket.team_id == team.id,
        SdTicket.assigned_agent_id.is_(None),
        SdTicket.status.in_(list(OPEN_TICKET_STATUSES)),
    ).limit(200).all())
    if not cands:
        raise HTTPException(404, "No unassigned tickets in this team's queue to distribute.")
    now = sla_util.now_utc()

    def _rank(t):
        breached = bool(t.sla_resolution_breached) or sla_util.resolution_state(t, now) == "breached"
        due = sla_util._aware(t.resolution_due_at)
        due_key = due.timestamp() if due else float("inf")
        try:
            pr = PRIORITY_ORDER.index(t.priority)
        except ValueError:
            pr = -1
        created = sla_util._aware(t.created_at)
        return (0 if breached else 1, due_key, -pr, created.timestamp() if created else 0.0)

    cands.sort(key=_rank)
    method = team.assignment_method if team.assignment_method in ("round_robin", "load_balanced") else "round_robin"
    # Live load snapshot (load-balanced) — incremented locally as we assign.
    counts = {str(r[0]): int(r[1] or 0) for r in
              db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
              .filter(SdTicket.assigned_agent_id.in_([u.id for u in pool]),
                      SdTicket.is_deleted == False,  # noqa: E712
                      SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
              .group_by(SdTicket.assigned_agent_id).all()}
    cursor = team.rr_last_user_id
    result = TeamDistributeResult(method=method)
    actor = getattr(user, "full_name", None) or "Team lead"
    for t in cands[:cap]:
        if method == "load_balanced":
            picked = min(pool, key=lambda u: counts.get(str(u.id), 0))
        else:
            picked = _round_robin(cursor, pool)
            cursor = picked.id
        t.assigned_agent_id = picked.id
        counts[str(picked.id)] = counts.get(str(picked.id), 0) + 1
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id, actor_name=actor,
                                action="assigned",
                                detail={"assigned_agent_id": str(picked.id),
                                        "by": "distribute", "method": method}))
        _dispatch_safe(db, EVT_TICKET_ASSIGNED, picked.id, t,
                       title=f"Assigned to you: {t.subject}", action_url="/user/support/tickets/my")
        result.assignments.append(TeamDistributeAssignment(
            ticket_id=t.id, ticket_number=t.ticket_number,
            agent_id=picked.id, agent_name=getattr(picked, "full_name", None) or picked.email))
    result.assigned = len(result.assignments)
    result.skipped = max(0, len(cands) - result.assigned)
    if method == "round_robin":
        team.rr_last_user_id = cursor
    write_audit(db, entity_type="ticket", op="distributed", entity_id=team.id,
                actor_id=user.id, request=request,
                details={"team": team.name, "method": method, "assigned": result.assigned})
    db.commit()
    return result


# ═══════════════════════ CHRONO DESK — the agent calendar ═══════════════════════
# One request per navigation. StaticPool serializes every request on a single DB
# connection, so the calendar returns EVERYTHING a view needs (typed events +
# zero-filled day buckets + holidays + business hours + meta) in one response —
# never a per-day/per-desk fan-out. All literal paths here sit BEFORE /{ticket_id}.

_CAL_DUE_KINDS = ("resolution_due", "response_due", "escalation_ack", "cadence_due",
                  "hold_resume", "vendor_due", "auto_close")
_CAL_HISTORY_KINDS = ("created", "resolved", "closed")
_CAL_ALL_KINDS = set(_CAL_DUE_KINDS) | set(_CAL_HISTORY_KINDS) | {"reminder"}
_CAL_DEFAULT_KINDS = set(_CAL_DUE_KINDS) | {"reminder"}
_CAL_MAX_SPAN_DAYS = 62          # a 6-week month grid is 42; leave slack for agenda views
_CAL_EVENT_CAP = 2000


def _cal_aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cal_seal(db: Session, user: User, mine: bool):
    """(is_su, ctx, cond|None) — the same seal ladder every sealed desk uses:
    personal lens → my assignments only; agent → command-center OR-seal; superuser → whole desk."""
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    if mine:
        return is_su, ctx, (SdTicket.assigned_agent_id == user.id)
    if is_su:
        return is_su, ctx, None
    return is_su, ctx, _command_center_filter(user, ctx)


def _cal_collect(db: Session, user: User, *, from_dt, to_dt, kinds: set, mine: bool,
                 team_id, priority, status_f):
    """Shared collector for the feed + the ICS export. ONE OR-of-ranges query over the
    kind columns, expanded into typed events in Python (a ticket can emit several kinds).
    Returns (events, is_su, ctx, truncated)."""
    now = sla_util.now_utc()
    is_su, ctx, seal = _cal_seal(db, user, mine)
    q = db.query(SdTicket).filter(SdTicket.is_deleted == False)  # noqa: E712
    if seal is not None:
        q = q.filter(seal)
    if team_id and (is_su or team_id in set(ctx["team_ids"])):   # narrow-only, like command-center
        q = q.filter(SdTicket.team_id == team_id)
    if priority and priority in _PRIORITIES:
        q = q.filter(SdTicket.priority == priority)
    if status_f and status_f in {s.value for s in TicketStatus}:
        q = q.filter(SdTicket.status == status_f)

    terminal = set(TERMINAL_TICKET_STATUSES)
    active = SdTicket.status.notin_(list(terminal))
    window = timedelta(days=SUPPORT_RESOLVED_AUTOCLOSE_DAYS)

    def rng(col, lo=from_dt, hi=to_dt):
        return and_(col.isnot(None), col >= lo, col <= hi)

    kind_conds = []
    if "resolution_due" in kinds:
        kind_conds.append(and_(rng(SdTicket.resolution_due_at), active))
    if "response_due" in kinds:
        kind_conds.append(and_(rng(SdTicket.response_due_at), active,
                               SdTicket.first_responded_at.is_(None)))
    if "escalation_ack" in kinds:
        kind_conds.append(and_(rng(SdTicket.escalation_response_due_at), active,
                               SdTicket.is_escalated == True,  # noqa: E712
                               SdTicket.escalation_acknowledged_at.is_(None)))
    if "cadence_due" in kinds:
        kind_conds.append(and_(rng(SdTicket.next_update_due_at), active))
    if "hold_resume" in kinds:
        kind_conds.append(and_(rng(SdTicket.hold_until),
                               SdTicket.status == TicketStatus.ON_HOLD.value))
    if "vendor_due" in kinds:
        kind_conds.append(and_(rng(SdTicket.vendor_due_at),
                               SdTicket.status == TicketStatus.PENDING_VENDOR.value))
    if "auto_close" in kinds:
        # auto_close_at = resolved_at + window ∈ [from,to]  ⇔  resolved_at ∈ [from-w, to-w]
        kind_conds.append(and_(rng(SdTicket.resolved_at, from_dt - window, to_dt - window),
                               SdTicket.status == TicketStatus.RESOLVED.value))
    if "created" in kinds:
        kind_conds.append(rng(SdTicket.created_at))
    if "resolved" in kinds:
        kind_conds.append(rng(SdTicket.resolved_at))
    if "closed" in kinds:
        kind_conds.append(rng(SdTicket.closed_at))

    tickets = []
    if kind_conds:
        tickets = (q.filter(or_(*kind_conds))
                   .order_by(SdTicket.created_at.desc()).limit(_CAL_EVENT_CAP).all())
    truncated = len(tickets) >= _CAL_EVENT_CAP

    events: list[CalendarEvent] = []

    def emit(t, kind, at, breached, sla_state=None):
        events.append(CalendarEvent(
            id=t.id, ticket_id=t.id, ticket_number=t.ticket_number, subject=t.subject,
            priority=t.priority, status=t.status, kind=kind, at=at,
            is_breached=bool(breached), sla_state=sla_state,
            assigned_agent_id=t.assigned_agent_id, team_id=t.team_id,
            is_major_incident=bool(getattr(t, "is_major_incident", False)),
        ))

    def in_range(at):
        return at is not None and from_dt <= at <= to_dt

    for t in tickets:
        act = t.status not in terminal
        if "resolution_due" in kinds and act:
            at = _cal_aware(t.resolution_due_at)
            if in_range(at):
                emit(t, "resolution_due", at, t.sla_resolution_breached,
                     sla_util.resolution_state(t, now))
        if "response_due" in kinds and act and t.first_responded_at is None:
            at = _cal_aware(t.response_due_at)
            if in_range(at):
                emit(t, "response_due", at, t.sla_response_breached,
                     sla_util.response_state(t, now))
        if "escalation_ack" in kinds and act and t.is_escalated and t.escalation_acknowledged_at is None:
            at = _cal_aware(t.escalation_response_due_at)
            if in_range(at):
                emit(t, "escalation_ack", at, at < now)
        if "cadence_due" in kinds and act:
            at = _cal_aware(t.next_update_due_at)
            if in_range(at):
                emit(t, "cadence_due", at, at < now)
        if "hold_resume" in kinds and t.status == TicketStatus.ON_HOLD.value:
            at = _cal_aware(t.hold_until)
            if in_range(at):
                emit(t, "hold_resume", at, at < now)
        if "vendor_due" in kinds and t.status == TicketStatus.PENDING_VENDOR.value:
            at = _cal_aware(t.vendor_due_at)
            if in_range(at):
                emit(t, "vendor_due", at, at < now)
        if "auto_close" in kinds and t.status == TicketStatus.RESOLVED.value and t.resolved_at:
            at = _cal_aware(t.resolved_at) + window
            if in_range(at):
                emit(t, "auto_close", at, False)
        if "created" in kinds:
            at = _cal_aware(t.created_at)
            if in_range(at):
                emit(t, "created", at, False)
        if "resolved" in kinds:
            at = _cal_aware(t.resolved_at)
            if in_range(at):
                emit(t, "resolved", at, False)
        if "closed" in kinds:
            at = _cal_aware(t.closed_at)
            if in_range(at):
                emit(t, "closed", at, False)

    # Reminders are OWNER-PRIVATE — always scoped to the caller, never the team seal.
    if "reminder" in kinds:
        rem_rows = (db.query(SdTicketReminder, SdTicket.ticket_number, SdTicket.subject,
                             SdTicket.priority, SdTicket.status)
                    .join(SdTicket, SdTicket.id == SdTicketReminder.ticket_id)
                    .filter(SdTicketReminder.user_id == user.id,
                            SdTicketReminder.remind_at >= from_dt,
                            SdTicketReminder.remind_at <= to_dt)
                    .order_by(SdTicketReminder.remind_at.asc()).limit(500).all())
        for r, num, subj, pri, st in rem_rows:
            at = _cal_aware(r.remind_at)
            events.append(CalendarEvent(
                id=r.id, ticket_id=r.ticket_id, ticket_number=num, subject=subj,
                priority=pri, status=st, kind="reminder", at=at,
                is_breached=bool(not r.done and at < now), note=r.note, done=r.done,
            ))

    # Assigned-agent names in one lookup (never enrich_tickets — too heavy for a feed).
    name_ids = list({e.assigned_agent_id for e in events if e.assigned_agent_id})
    if name_ids:
        names = _user_names(db, name_ids)
        for e in events:
            if e.assigned_agent_id:
                e.assigned_agent_name = names.get(str(e.assigned_agent_id))

    events.sort(key=lambda e: e.at)
    return events, is_su, ctx, truncated


def _cal_parse_kinds(kinds: Optional[str]) -> set:
    if not kinds:
        return set(_CAL_DEFAULT_KINDS)
    asked = {k.strip() for k in kinds.split(",") if k.strip()}
    picked = asked & _CAL_ALL_KINDS
    if not picked:
        raise HTTPException(422, f"No valid kinds in '{kinds}'. Valid: {sorted(_CAL_ALL_KINDS)}")
    return picked


@router.get("/calendar", response_model=CalendarFeedResponse)
def my_ticket_calendar(
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    kinds: Optional[str] = Query(None, description="csv of event kinds (default: forward kinds + reminders)"),
    mine: bool = Query(False, description="Only tickets assigned to me"),
    team_id: Optional[UUID] = Query(None),
    priority: Optional[str] = Query(None),
    status_f: Optional[str] = Query(None, alias="status"),
    tz_offset: int = Query(0, ge=-840, le=840,
                           description="Caller minutes EAST of UTC (JS: -new Date().getTimezoneOffset())"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """The Chrono Desk feed: sealed multi-kind events + zero-filled local-day buckets +
    HR holidays + team business hours + meta, in ONE response. Before /{ticket_id}."""
    from_dt, to_dt = _cal_aware(from_), _cal_aware(to)
    if to_dt <= from_dt:
        raise HTTPException(422, "'to' must be after 'from'")
    if (to_dt - from_dt) > timedelta(days=_CAL_MAX_SPAN_DAYS):
        raise HTTPException(422, f"Range too wide — max {_CAL_MAX_SPAN_DAYS} days")
    picked = _cal_parse_kinds(kinds)

    events, is_su, ctx, truncated = _cal_collect(
        db, user, from_dt=from_dt, to_dt=to_dt, kinds=picked,
        mine=mine, team_id=team_id, priority=priority, status_f=status_f)

    # ── Local-day buckets (tz_offset guards the classic UTC→IST off-by-one) ──
    off = timedelta(minutes=tz_offset)
    now = sla_util.now_utc()

    def lkey(dt):
        return (dt + off).date()

    day0, day1 = lkey(from_dt), lkey(to_dt)
    ndays = (day1 - day0).days + 1
    buckets = {day0 + timedelta(days=i): {"counts": {}, "load": 0, "breach": 0}
               for i in range(max(1, ndays))}
    due_kind_set = set(_CAL_DUE_KINDS)
    for e in events:
        b = buckets.get(lkey(e.at))
        if b is None:
            continue
        b["counts"][e.kind] = b["counts"].get(e.kind, 0) + 1
        if e.kind in due_kind_set:
            b["load"] += 1
        if e.is_breached:
            b["breach"] += 1
    days = [CalendarDay(date=d.isoformat(), counts=buckets[d]["counts"],
                        load=buckets[d]["load"], breach=buckets[d]["breach"])
            for d in sorted(buckets)]

    # ── Holidays (HR master; APPLIED/live rows only). Read-only, best-effort. ──
    holidays = []
    try:
        from app.models.hr.holiday import Holiday
        hrows = (db.query(Holiday)
                 .filter(Holiday.is_deleted == False, Holiday.is_active == True,  # noqa: E712
                         Holiday.date >= day0, Holiday.date <= day1)
                 .order_by(Holiday.date.asc()).all())
        holidays = [CalendarHoliday(
            date=h.date.isoformat(), name=h.name,
            holiday_type=(getattr(h.holiday_type, "value", None) or
                          (str(h.holiday_type) if h.holiday_type else None)),
        ) for h in hrows]
    except Exception:
        holidays = []

    # ── Business hours — the caller's first team that declares them ──
    business = None
    for tm in ctx["teams"]:
        bh = getattr(tm, "business_hours", None) or {}
        if bh and (bh.get("start") or bh.get("days")):
            try:
                bdays = [int(d) for d in (bh.get("days") or [])]
            except (TypeError, ValueError):
                bdays = []
            business = CalendarBusiness(tz=bh.get("tz"), days=bdays,
                                        start=bh.get("start"), end=bh.get("end"),
                                        team_name=tm.name)
            break

    # ── Meta: triage numbers + the "next open window" suggestion inputs ──
    meta = CalendarMeta(total_events=len(events), truncated=truncated)
    today_l = lkey(now)
    tb = buckets.get(today_l)
    meta.due_today = tb["load"] if tb else 0
    horizon = now + timedelta(days=7)
    meta.breach_risk_7d = sum(1 for e in events
                              if e.kind in due_kind_set and not e.is_breached
                              and now <= e.at <= horizon)
    meta.breached_open = sum(1 for e in events if e.is_breached and e.kind in due_kind_set)
    meta.holds_resuming = sum(1 for e in events if e.kind == "hold_resume")
    meta.reminders = sum(1 for e in events if e.kind == "reminder")
    loads = [(d, buckets[d]["load"]) for d in sorted(buckets)]
    if loads:
        bd, bc = max(loads, key=lambda x: x[1])
        if bc > 0:
            meta.busiest_day, meta.busiest_count = bd.isoformat(), bc
        nonzero = [c for _, c in loads if c > 0]
        if nonzero:
            threshold = max(6, round(2 * (sum(nonzero) / len(nonzero))))
            meta.overloaded_days = [d.isoformat() for d, c in loads if c >= threshold]
        meta.next_open_day = next((d.isoformat() for d, c in loads
                                   if d > today_l and c == 0), None)
    return CalendarFeedResponse(events=events, days=days, holidays=holidays,
                                business=business, meta=meta)


_ICS_KIND_LABEL = {
    "resolution_due": "Resolution due", "response_due": "First response due",
    "escalation_ack": "Escalation ACK due", "cadence_due": "Status update due",
    "hold_resume": "Hold auto-resumes", "vendor_due": "Vendor reply due",
    "auto_close": "Auto-closes", "reminder": "Reminder",
    "created": "Opened", "resolved": "Resolved", "closed": "Closed",
}


def _ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@router.get("/calendar/export.ics")
def my_ticket_calendar_ics(
    from_: datetime = Query(..., alias="from"),
    to: datetime = Query(...),
    kinds: Optional[str] = Query(None),
    mine: bool = Query(False),
    team_id: Optional[UUID] = Query(None),
    priority: Optional[str] = Query(None),
    status_f: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """ICS export of the sealed calendar feed — drop the desk's deadlines into
    Outlook/Google. UTC VEVENTs; same seal + kinds as the feed. Before /{ticket_id}."""
    from_dt, to_dt = _cal_aware(from_), _cal_aware(to)
    if to_dt <= from_dt:
        raise HTTPException(422, "'to' must be after 'from'")
    if (to_dt - from_dt) > timedelta(days=_CAL_MAX_SPAN_DAYS):
        raise HTTPException(422, f"Range too wide — max {_CAL_MAX_SPAN_DAYS} days")
    picked = _cal_parse_kinds(kinds)
    events, _, _, _ = _cal_collect(
        db, user, from_dt=from_dt, to_dt=to_dt, kinds=picked,
        mine=mine, team_id=team_id, priority=priority, status_f=status_f)
    stamp = sla_util.now_utc().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0",
             "PRODID:-//FourConnect//Support Desk Chrono//EN", "CALSCALE:GREGORIAN",
             "X-WR-CALNAME:FourConnect Support Desk"]
    for e in events:
        dt = e.at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        label = _ICS_KIND_LABEL.get(e.kind, e.kind)
        summary = f"[{e.ticket_number or 'SD'}] {label} — {e.subject or ''}"
        lines += ["BEGIN:VEVENT",
                  f"UID:{e.kind}-{e.id}@fourconnect",
                  f"DTSTAMP:{stamp}",
                  f"DTSTART:{dt}",
                  f"SUMMARY:{_ics_escape(summary)}",
                  f"CATEGORIES:{e.kind.upper()}",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return Response(content="\r\n".join(lines) + "\r\n", media_type="text/calendar",
                    headers={"Content-Disposition":
                             'attachment; filename="fourconnect-support-calendar.ics"'})


@router.get("/reminders", response_model=list[ReminderResponse])
def list_my_reminders(include_done: bool = Query(False),
                      db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The caller's own follow-up pins (owner-private). Before /{ticket_id}."""
    q = (db.query(SdTicketReminder, SdTicket.ticket_number, SdTicket.subject)
         .join(SdTicket, SdTicket.id == SdTicketReminder.ticket_id)
         .filter(SdTicketReminder.user_id == user.id))
    if not include_done:
        q = q.filter(SdTicketReminder.done == False)  # noqa: E712
    rows = q.order_by(SdTicketReminder.remind_at.asc()).limit(200).all()
    out = []
    for r, num, subj in rows:
        resp = ReminderResponse.model_validate(r)
        resp.ticket_number, resp.subject = num, subj
        out.append(resp)
    return out


@router.post("/reminders", response_model=ReminderResponse, status_code=status.HTTP_201_CREATED)
def create_my_reminder(payload: ReminderCreate,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Pin a follow-up on any ticket inside the caller's seal. Owner-private; carries no
    workflow authority (deliberately NOT an SLA write)."""
    is_su, ctx, seal = _cal_seal(db, user, False)
    q = db.query(SdTicket).filter(SdTicket.id == payload.ticket_id,
                                  SdTicket.is_deleted == False)  # noqa: E712
    if seal is not None:
        q = q.filter(seal)
    t = q.first()
    if not t:
        raise HTTPException(404, "Ticket not found (or outside your desk)")
    if t.merged_into_id:
        master = db.query(SdTicket.ticket_number).filter(SdTicket.id == t.merged_into_id).scalar()
        raise HTTPException(409, f"{t.ticket_number} was merged into {master or 'another ticket'} — pin the reminder on that record instead.")
    remind_at = _cal_aware(payload.remind_at)
    if remind_at <= sla_util.now_utc():
        raise HTTPException(422, "Reminder must be in the future")
    note = (payload.note or "").strip()[:300] or None
    r = SdTicketReminder(ticket_id=t.id, user_id=user.id, remind_at=remind_at, note=note)
    db.add(r)
    db.commit()
    db.refresh(r)
    resp = ReminderResponse.model_validate(r)
    resp.ticket_number, resp.subject = t.ticket_number, t.subject
    return resp


@router.patch("/reminders/{reminder_id}", response_model=ReminderResponse)
def update_my_reminder(reminder_id: UUID, payload: ReminderUpdate,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(SdTicketReminder).filter(SdTicketReminder.id == reminder_id,
                                          SdTicketReminder.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "Reminder not found")
    if payload.remind_at is not None:
        r.remind_at = _cal_aware(payload.remind_at)
    if payload.note is not None:
        r.note = payload.note.strip()[:300] or None
    if payload.done is not None:
        r.done = bool(payload.done)
    db.commit()
    db.refresh(r)
    t = db.query(SdTicket.ticket_number, SdTicket.subject).filter(SdTicket.id == r.ticket_id).first()
    resp = ReminderResponse.model_validate(r)
    if t:
        resp.ticket_number, resp.subject = t[0], t[1]
    return resp


@router.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_reminder(reminder_id: UUID,
                       db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    r = db.query(SdTicketReminder).filter(SdTicketReminder.id == reminder_id,
                                          SdTicketReminder.user_id == user.id).first()
    if not r:
        raise HTTPException(404, "Reminder not found")
    db.delete(r)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_my_ticket(
    payload: TicketCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.priority not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{payload.priority}'")
    if payload.ticket_type not in _TYPES:
        raise HTTPException(422, f"Invalid ticket_type '{payload.ticket_type}'")
    source = payload.source if payload.source in _SOURCES else TicketSource.PORTAL.value

    # Role-adaptive team gate (the rule). If this classification routes to a real support
    # team, who is checked depends on the caller — and tickets that match NO team go to
    # triage and are always allowed. Mirrors the create-page lock so the API can't be
    # bypassed directly.
    #   • admin (superuser) — no restriction; an explicit assignee is honoured as-is.
    #   • reporting manager — validated by the ASSIGNEE's membership, not their own: a
    #     manager may assign only to a direct report / led-team member who is also on the
    #     team this routes to.
    #   • everyone else (self) — must be a member of the handling team to raise it here.
    # Union of EVERY team that handles this type/category — membership on ANY of them
    # counts (Tier 1 + Tier 2 both owning a type must not block a Tier 2 member).
    handling = teams_handling(db, payload.ticket_type, payload.category_id)
    handling_member_ids = set()
    for tm in handling:
        handling_member_ids |= _team_members_of(db, tm.id)
    ctx = _team_context(db, user)
    is_admin = bool(getattr(user, "is_superuser", False))
    is_manager = bool(ctx["reports"])
    manager_assignee = None
    if is_admin:
        manager_assignee = payload.assigned_agent_id
    elif is_manager:
        # Self-claim (assigning oneself) goes through the assign_me path below, not here.
        if payload.assigned_agent_id and payload.assigned_agent_id != user.id:
            target = payload.assigned_agent_id
            if target not in (ctx["reports"] | ctx["led_member_ids"]):
                raise HTTPException(403, "You can only assign tickets to your own team members.")
            if handling and target not in handling_member_ids:
                raise HTTPException(422, "The agent you assigned isn't on a team that handles this request type. Pick an agent who is.")
            manager_assignee = target
    elif user.id not in handling_member_ids:
        # SELF — membership in the handling union is the rule, agent flag or not: team
        # membership auto-grants is_support_agent, so an agent exemption voids the gate.
        # An EMPTY union blocks too — a type no team owns must not slip into triage from
        # here; only an admin or a reporting manager may raise an unclaimed type.
        if handling:
            raise HTTPException(403, "You're not on a team that handles this request type — pick a request type your team handles, or ask one of the owning teams to raise it.")
        raise HTTPException(403, f"No support team handles '{payload.ticket_type}' requests yet — pick a different request type, or ask an admin to set up routing for it.")

    number = generate_ticket_number(db)
    pkg = resolve_sla_package(db, payload.sla_package_id, payload.organization_id)
    rd, rsd = sla_util.compute_deadlines(pkg, payload.priority)

    # Template provenance — stamp only if the template still exists; a stale or
    # bogus id is silently dropped (a template must never block ticket creation).
    template_id = None
    if payload.template_id:
        from app.models.support_desk.workspace import SdTicketTemplate
        _tpl = db.query(SdTicketTemplate.id).filter(
            SdTicketTemplate.id == payload.template_id,
            SdTicketTemplate.is_deleted == False,  # noqa: E712
        ).first()
        template_id = payload.template_id if _tpl else None

    t = SdTicket(
        ticket_number=number,
        subject=payload.subject,
        description=payload.description,
        category_id=payload.category_id,
        subcategory_id=payload.subcategory_id,
        ticket_type=payload.ticket_type,
        priority=payload.priority,
        impact=payload.impact,
        urgency=payload.urgency,
        source=source,
        template_id=template_id,
        status=TicketStatus.OPEN.value,
        organization_id=payload.organization_id,
        customer_id=payload.customer_id,
        is_internal=not payload.organization_id,
        raised_by_user_id=user.id,
        contact_name=payload.contact_name or getattr(user, "full_name", None),
        contact_email=payload.contact_email or getattr(user, "email", None),
        contact_phone=payload.contact_phone,
        department=payload.department,
        location=payload.location,
        business_impact=payload.business_impact,
        affected_users=payload.affected_users,
        revenue_impact=payload.revenue_impact,
        vendor_name=payload.vendor_name,
        linked_change_id=payload.linked_change_id,
        linked_problem_id=payload.linked_problem_id,
        sla_package_id=pkg.id if pkg else None,
        response_due_at=rd, resolution_due_at=rsd,
        attachments=payload.attachments or [],
        tags=payload.tags or [],
        created_by_id=user.id,
    )
    db.add(t)
    db.flush()
    from app.models.support_desk.ticket import SdTicketActivity
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Employee",
                            action="created", detail={"ticket_number": number}))
    write_audit(db, entity_type="ticket", op="created", entity_id=t.id,
                actor_id=user.id, request=request,
                details={"ticket_number": number, "self_service": True})
    # Routing chain (first-match): admin-authored automation rules → category/type
    # router (+ auto-assign when the routed queue is configured for it) → default-queue
    # fallback. Notify the chosen agent. Best-effort.
    evaluate_rules(db, t)
    routed = route_and_assign(db, t)
    apply_default_queue(db, t)
    # Create-time self-claim: only honoured when the requester may actually WORK the
    # ticket (a support agent, or a member of the team that handles it) — mirrors the
    # "team members/agents only" rule. Overrides any auto-assignment.
    if getattr(payload, "assign_me", False) and _can_work(db, t, user):
        t.assigned_agent_id = user.id
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                                actor_name=getattr(user, "full_name", None) or "Employee",
                                action="assigned",
                                detail={"assigned_agent_id": str(user.id), "self_claim": True}))
    elif manager_assignee:
        # Reporting-manager assignment (validated above against the routed team).
        t.assigned_agent_id = manager_assignee
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                                actor_name=getattr(user, "full_name", None) or "Manager",
                                action="assigned",
                                detail={"assigned_agent_id": str(manager_assignee), "by": "manager"}))
        _dispatch_safe(db, EVT_TICKET_ASSIGNED, manager_assignee, t,
                       title=f"Assigned to you: {t.subject}", action_url="/user/support/tickets/my")
    elif routed.get("assigned_user_id"):
        _dispatch_safe(db, EVT_TICKET_ASSIGNED, routed["assigned_user_id"], t,
                       title=f"Assigned to you: {t.subject}", action_url="/user/support/tickets/my")
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
def get_my_ticket(ticket_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t, can_work, _ = _load_visible(db, ticket_id, user)
    maybe_auto_close(db, t)   # lazily close if the reopen window has elapsed
    enrich_ticket(db, t)
    resp = TicketDetailResponse.model_validate(t)
    resp.viewer_can_work = can_work
    if not can_work:
        resp.comments = [c for c in resp.comments if not c.is_internal]  # requester never sees internal notes (incl. vendor)
        _scrub_vendor_internals(resp)                                    # …nor which third party the desk handed it to
    return resp


@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def reply_my_ticket(
    ticket_id: UUID,
    payload: CommentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A reply on a ticket I can see. The author kind adapts: a WORKER (agent / assignee /
    collaborator / team member) posts as STAFF and may flag the note internal; the
    requester posts as CUSTOMER (and stamps the pending-customer freshness clock)."""
    t, can_work, is_requester = _load_visible(db, ticket_id, user)
    nowt = sla_util.now_utc()
    # A worker who isn't the requester replies as staff; everyone else replies as the customer.
    as_staff = can_work and not is_requester
    internal = bool(payload.is_internal) and as_staff
    kind = CommentAuthorKind.STAFF.value if as_staff else CommentAuthorKind.CUSTOMER.value
    c = SdTicketComment(
        ticket_id=t.id, author_user_id=user.id,
        author_name=getattr(user, "full_name", None) or ("Support" if as_staff else "Employee"),
        author_kind=kind, body=payload.body,
        is_internal=internal, attachments=payload.attachments or [],
    )
    db.add(c)
    from app.models.support_desk.ticket import SdTicketActivity
    if as_staff:
        # First public staff reply stops the response-SLA clock.
        if not internal and t.first_responded_at is None:
            t.first_responded_at = nowt
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                                actor_name=getattr(user, "full_name", None) or "Support",
                                action="internal_note" if internal else "replied",
                                detail={"preview": payload.body[:80]}))
        # Public staff reply notifies the requester; internal notes stay silent.
        if not internal and t.raised_by_user_id and t.raised_by_user_id != user.id:
            _dispatch_safe(db, EVT_TICKET_REPLIED, t.raised_by_user_id, t,
                           title=f"Support replied on {t.ticket_number}", action_url="/user/support/tickets/my")
    else:
        # The requester just replied — stamp it so the pending-customer reminder workflow
        # measures freshness from the last customer reply, and notify the assignee.
        t.last_customer_reply_at = nowt
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                                actor_name=getattr(user, "full_name", None) or "Employee",
                                action="replied", detail={"preview": payload.body[:80]}))
        # Loophole fix: a reply from the customer puts the ball back in the desk's court —
        # pull an awaiting-customer ticket back into active work (SLA un-freezes) so it stops
        # sitting silently in "pending" forever after the customer answered.
        # And if the ticket was already RESOLVED (within the reopen window), the reply means
        # the fix didn't hold — auto-reopen it (source='portal') instead of letting the
        # message land silently while the auto-close sweep buries it.
        if not reactivate_on_customer_reply(db, t, nowt):
            auto_reopen_on_customer_reply(db, t, getattr(user, "full_name", None) or "Employee", nowt)
        if t.assigned_agent_id:
            try:
                from app.utils.hr.notify import dispatch
                dispatch(db, EVT_TICKET_REPLIED, t.assigned_agent_id,
                         context={"title": f"Customer reply on {t.ticket_number}",
                                  "message": t.subject, "action_url": f"/admin/support-desk/tickets?ticket={t.id}"},
                         audience="SUPPORT")
            except Exception:
                pass
    write_audit(db, entity_type="ticket", op="commented", entity_id=t.id,
                actor_id=user.id, request=request, details={"by": "staff" if as_staff else "requester", "internal": internal})
    db.commit()
    db.refresh(c)
    return c


@router.post("/{ticket_id}/run-template/{tpl_id}", response_model=CommentResponse,
             status_code=status.HTTP_201_CREATED)
def run_template_on_my_ticket(
    ticket_id: UUID,
    tpl_id: UUID,
    payload: TemplateRunRequest,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Zendesk-style MACRO — run a template on an EXISTING ticket: post its rendered
    body as an internal note or public reply, optionally adopt the template's priority
    and merge its tags. Owner-tier gated (assignee / collaborator / lead / manager /
    superuser) like every other mutation on this router.

    ``payload.body`` arrives RENDERED — substitution stays client-side (the single
    engine in templateVariables.js, reviewed by the agent in the run modal); template
    provenance is stamped on the activity row. Usage counts here (kind='macro') —
    the /apply endpoint is for new-ticket prefill and must NOT also be called.
    """
    t, can_work, _ = _load_visible(db, ticket_id, user, require_work=True)
    _require_self_actor(db, t, user, "run a template on it")
    if t.archived_at is not None:
        raise HTTPException(409, "Ticket is archived — deep-storage records are read-only")

    tpl = db.query(SdTicketTemplate).filter(
        SdTicketTemplate.id == tpl_id, SdTicketTemplate.is_deleted == False,  # noqa: E712
    ).first()
    if not tpl or not _template_visible(db, tpl, user):
        raise HTTPException(404, "Template not found")
    if tpl.status == "archived":
        raise HTTPException(409, "Template is archived")
    if tpl.status == "draft" and not (user.is_superuser or _own_personal(tpl, user)):
        raise HTTPException(409, "Template is a draft")

    mode = (payload.mode or "internal_note").strip()
    if mode not in {"internal_note", "reply"}:
        raise HTTPException(422, "mode must be 'internal_note' or 'reply'")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(422, "Rendered body is required")

    nowt = sla_util.now_utc()
    internal = mode == "internal_note"
    actor_name = getattr(user, "full_name", None) or "Support"
    c = SdTicketComment(
        ticket_id=t.id, author_user_id=user.id, author_name=actor_name,
        author_kind=CommentAuthorKind.STAFF.value, body=body,
        is_internal=internal, attachments=[],
    )
    db.add(c)

    detail = {"template_id": str(tpl.id), "name": tpl.name,
              "version": tpl.version or 1, "mode": mode}
    if not internal:
        # Mirrors reply_my_ticket: first public staff reply stops the response-SLA
        # clock, and the requester hears about it.
        if t.first_responded_at is None:
            t.first_responded_at = nowt
        if t.raised_by_user_id and t.raised_by_user_id != user.id:
            _dispatch_safe(db, EVT_TICKET_REPLIED, t.raised_by_user_id, t,
                           title=f"Support replied on {t.ticket_number}",
                           action_url="/user/support/tickets/my")
    if payload.apply_priority and tpl.priority and tpl.priority in _PRIORITIES \
            and tpl.priority != t.priority:
        detail["priority"] = {"from": t.priority, "to": tpl.priority}
        t.priority = tpl.priority
    if payload.merge_tags and (tpl.tags or []):
        added = [x for x in (tpl.tags or []) if x and x not in (t.tags or [])]
        if added:
            detail["tags_added"] = added
            t.tags = list(t.tags or []) + added   # REASSIGN — JSONB in-place edits aren't tracked

    # Usage: desk-global counters + the per-agent event (kind='macro').
    tpl.usage_count = SdTicketTemplate.usage_count + 1   # atomic SQL increment
    tpl.last_used_at = func.now()
    tpl.last_used_by_id = user.id
    db.add(SdTemplateUsageEvent(template_id=tpl.id, user_id=user.id,
                                ticket_id=t.id, kind="macro"))
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id, actor_name=actor_name,
                            action="template_run", detail=detail))
    write_audit(db, entity_type="ticket_template", op="macro_applied", entity_id=tpl.id,
                actor_id=user.id, request=request,
                details={"ticket_id": str(t.id), "ticket_number": t.ticket_number,
                         "name": tpl.name, "mode": mode})
    db.commit()
    db.refresh(c)
    return c


@router.post("/{ticket_id}/csat", response_model=TicketResponse)
def rate_my_ticket(
    ticket_id: UUID,
    payload: TicketCsat,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _own(db, ticket_id, user)
    if t.status not in (TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value):
        raise HTTPException(409, "You can only rate a resolved ticket")
    # Sealed-record rule (mirrors the agent router): a rating on a CLOSED ticket is the
    # verdict of record. Rating a closed ticket ONCE stays allowed (the survey may land
    # after the auto-close sweep); rewriting an existing one does not.
    if t.status == TicketStatus.CLOSED.value and t.csat_score is not None:
        raise HTTPException(409, "This ticket is closed and already rated — the rating is the verdict of record.")
    t.csat_score = payload.csat_score
    t.csat_comment = payload.csat_comment
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────── Requester self-edit / withdraw / reopen ───────────────────────
@router.patch("/{ticket_id}", response_model=TicketResponse)
def edit_my_ticket(
    ticket_id: UUID,
    payload: SelfTicketUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A requester refines their OWN ticket — but only while it is still OPEN, i.e.
    before an agent engages. Once it moves to in_progress/pending/resolved the
    classification is locked (agents own it) → 409. Narrow field set only."""
    t = _own(db, ticket_id, user)
    if t.status != TicketStatus.OPEN.value:
        raise HTTPException(409, "This ticket can no longer be edited — support has already picked it up.")
    data = payload.model_dump(exclude_unset=True)
    if data.get("priority") and data["priority"] not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{data['priority']}'")
    changed = {}
    for k, v in data.items():
        if getattr(t, k, None) != v:
            changed[k] = v
            setattr(t, k, v)
    if changed:
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                                actor_name=getattr(user, "full_name", None) or "Employee",
                                action="updated", detail={"changes": list(changed.keys()), "by": "requester"}))
        write_audit(db, entity_type="ticket", op="updated", entity_id=t.id,
                    actor_id=user.id, request=request, details={"by": "requester", "changes": list(changed.keys())})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/withdraw", response_model=TicketResponse)
def withdraw_my_ticket(
    ticket_id: UUID,
    payload: SelfTicketWithdraw,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Requester withdraws their own request (recoverable cancel). Closes the ticket
    with resolution_code='cancelled' + a reason, stops the SLA clock, keeps it in
    history. It can later be reopened. Cannot withdraw an already-terminal ticket."""
    t = _own(db, ticket_id, user)
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is already resolved/closed — nothing to withdraw.")
    nowt = sla_util.now_utc()
    sla_util.apply_pause_transition(t, t.status, TicketStatus.CLOSED.value, nowt)
    t.status = TicketStatus.CLOSED.value
    t.resolution_code = "cancelled"
    t.resolution_summary = payload.reason
    t.closed_at = nowt
    t.closed_by_id = user.id
    if t.resolved_at is None:
        t.resolved_at = nowt
        t.resolved_by_id = user.id
    sla_util.recompute_breach_flags(t, nowt)
    db.add(SdTicketComment(ticket_id=t.id, author_user_id=user.id,
                           author_name=getattr(user, "full_name", None) or "Employee",
                           author_kind=CommentAuthorKind.SYSTEM.value,
                           body=f"Ticket withdrawn by the requester. Reason: {payload.reason}", is_internal=False))
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Employee",
                            action="withdrawn", detail={"reason": payload.reason}))
    _dispatch_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id, t,
                   title=f"Ticket {t.ticket_number} withdrawn by requester",
                   action_url="/admin/support-desk/tickets")
    write_audit(db, entity_type="ticket", op="withdrawn", entity_id=t.id,
                actor_id=user.id, request=request, details={"reason": payload.reason})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/reopen", response_model=TicketResponse)
def reopen_my_ticket(
    ticket_id: UUID,
    payload: SelfTicketReopen,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Requester reopens their OWN resolved ticket (the issue recurred). Only a
    RESOLVED ticket can be reopened by the requester — a fully CLOSED or WITHDRAWN
    ticket needs an agent. Bookkeeping (count/source/latency/failed-fix snapshot/fresh
    re-resolution SLA + the 'reopened' activity) is owned by _common.apply_reopen —
    single-writer rule, in lockstep with the agent transition and the portal auto-reopen."""
    t = _own(db, ticket_id, user)
    if t.status != TicketStatus.RESOLVED.value:
        raise HTTPException(409, "Only a resolved ticket can be reopened. Closed or withdrawn tickets need support to reopen.")
    nowt = sla_util.now_utc()
    apply_reopen(db, t, user.id, getattr(user, "full_name", None) or "Employee",
                 source=ReopenSource.REQUESTER.value, reason=payload.reason,
                 reason_code=payload.reason_code, nowt=nowt)
    t.status = TicketStatus.IN_PROGRESS.value
    db.add(SdTicketComment(ticket_id=t.id, author_user_id=user.id,
                           author_name=getattr(user, "full_name", None) or "Employee",
                           author_kind=CommentAuthorKind.CUSTOMER.value,
                           body=f"Reopened by requester: {payload.reason}", is_internal=False))
    _dispatch_safe(db, EVT_TICKET_REOPENED, t.assigned_agent_id, t,
                   title=f"Ticket {t.ticket_number} reopened by requester",
                   action_url="/admin/support-desk/tickets/reopened")
    write_audit(db, entity_type="ticket", op="reopened", entity_id=t.id,
                actor_id=user.id, request=request,
                details={"reason": payload.reason, "reason_code": payload.reason_code, "by": "requester"})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────── Team-scoped assign / reassign ───────────────────────
@router.get("/{ticket_id}/assignees", response_model=list[AssigneeOption])
def ticket_assignees(ticket_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Who the current user may route THIS ticket to: members of the ticket's owning
    team (when the caller is on it), the caller's direct reports, and the caller. Powers
    the in-drawer reassignment picker — so a team member can hand a ticket to a teammate."""
    t, can_work, _ = _load_visible(db, ticket_id, user, require_work=True)
    ctx = _team_context(db, user)
    on_ticket_team = bool(t.team_id) and t.team_id in ctx["team_ids"]
    team = db.query(SdTeam).filter(SdTeam.id == t.team_id).first() if on_ticket_team else None
    roles = (team.member_roles or {}) if team else {}
    cand: dict[str, dict] = {}
    # the ticket's team members (the precise reassignment pool)
    if team:
        for m in (team.member_ids or []):
            mid = str(m)
            cand[mid] = {"role": ("lead" if mid == str(team.lead_user_id) else roles.get(mid, "agent"))}
        if team.lead_user_id:
            cand.setdefault(str(team.lead_user_id), {"role": "lead"})
    # plus my direct reports (manager path)
    for r in ctx["reports"]:
        cand.setdefault(str(r), {"role": "report"})
    # plus me
    cand.setdefault(str(user.id), {"role": "me"})
    # an agent may route to anyone — but we still return the relevant pool by default
    ids = {_as_uuid(k) for k in cand}
    ids = {i for i in ids if i}
    rows = db.query(User).filter(User.id.in_(ids), User.is_active == True).all() if ids else []  # noqa: E712
    out = []
    for u in rows:
        meta = cand.get(str(u.id), {})
        out.append(AssigneeOption(
            id=u.id, name=u.full_name or u.email or "Member", email=u.email,
            role=meta.get("role"),
            is_agent=bool(getattr(u, "is_support_agent", False) or u.is_superuser),
            is_current=str(t.assigned_agent_id) == str(u.id),
        ))
    out.sort(key=lambda o: (o.role != "me", o.role != "lead", (o.name or "").lower()))
    return out


@router.post("/{ticket_id}/assign", response_model=TicketResponse)
def manager_assign_ticket(
    ticket_id: UUID,
    payload: SelfTicketAssign,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Route a ticket to another person. Allowed when the caller is a support agent, a
    reporting manager, a team lead, or any MEMBER of the team the ticket belongs to — so
    a team member can re-assign within their own team. The assignee must be in reach:
    themselves, a direct report, a member of a team they lead, or — when the ticket
    belongs to one of their teams — a member of that same team."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket not found")
    ctx = _route_authz(db, t, user, payload.assigned_agent_id, verb="route")
    prev = t.assigned_agent_id
    t.assigned_agent_id = payload.assigned_agent_id
    _stamp_team_on_claim(t, ctx, payload.assigned_agent_id)
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Member",
                            action="assigned", detail={"assigned_agent_id": str(payload.assigned_agent_id), "by": "team"}))
    if t.assigned_agent_id and t.assigned_agent_id != prev:
        _dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                       title=f"Assigned to you: {t.subject}", action_url="/user/support/tickets/my")
    write_audit(db, entity_type="ticket", op="assigned", entity_id=t.id,
                actor_id=user.id, request=request, details={"assigned_agent_id": str(payload.assigned_agent_id), "by": "team"})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/handoff", response_model=TicketResponse)
def handoff_ticket(
    ticket_id: UUID,
    payload: TicketHandoff,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """First-class agent→agent HANDOFF (Team Ops desk): an audited, reason-coded transfer
    of an active ticket to a teammate. Same reach rules as /assign (agent / manager /
    team lead / member of the ticket's team; target must be in the caller's pool), plus:
    the ticket must be live (terminal ⇒ 409 — reopen first), the target must differ from
    the current owner, and the timeline gets a dedicated 'handoff' entry carrying
    from → to + the coded reason so rebalance analytics stay first-class."""
    if payload.reason_code and payload.reason_code not in HANDOFF_REASON_CODES:
        raise HTTPException(422, f"Unknown handoff reason '{payload.reason_code}'. Use one of: {', '.join(HANDOFF_REASON_CODES)}")
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket not found")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — reopen it before handing it off.")
    if str(payload.to_agent_id) == str(t.assigned_agent_id or ""):
        raise HTTPException(409, "That teammate already owns this ticket.")
    target = db.query(User).filter(User.id == payload.to_agent_id, User.is_active == True).first()  # noqa: E712
    if not target:
        raise HTTPException(422, "The selected teammate does not exist or is inactive.")
    ctx = _route_authz(db, t, user, payload.to_agent_id, verb="hand off")
    prev_id = t.assigned_agent_id
    names = _user_names(db, [x for x in (prev_id, payload.to_agent_id, user.id) if x])
    t.assigned_agent_id = payload.to_agent_id
    _stamp_team_on_claim(t, ctx, payload.to_agent_id)
    note = (payload.note or "").strip()
    detail = {
        "from_id": str(prev_id) if prev_id else None,
        "from_name": names.get(str(prev_id)) if prev_id else None,
        "to_id": str(payload.to_agent_id),
        "to_name": names.get(str(payload.to_agent_id)) or getattr(target, "full_name", None),
        **({"reason_code": payload.reason_code} if payload.reason_code else {}),
        **({"note": note} if note else {}),
    }
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Member",
                            action="handoff", detail=detail))
    _dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                   title=f"Handed off to you: {t.subject}", action_url="/user/support/tickets/my")
    write_audit(db, entity_type="ticket", op="handoff", entity_id=t.id,
                actor_id=user.id, request=request, details=detail)
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/claim", response_model=TicketResponse)
def claim_ticket(
    ticket_id: UUID,
    payload: ClaimTicket,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deliberate self-claim of one unowned ticket from the Unassigned queue. ENFORCES
    eligibility: the caller must be a superuser, on the ticket's team, or on a team that
    handles its request type/category (untriaged) — the same rule the queue lists by, so
    it's un-leakable. Assigns to the caller + stamps the team when untriaged."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket not found")
    is_su = bool(getattr(user, "is_superuser", False))
    ctx = _team_context(db, user)
    if not (_is_agent(user) or ctx["teams"]):
        raise HTTPException(403, "Only a support agent or a member of a support team can claim tickets.")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is already resolved or closed.")
    if t.assigned_agent_id is not None:
        if str(t.assigned_agent_id) == str(user.id):
            return TicketResponse.model_validate(enrich_ticket(db, t))   # idempotent — already mine
        raise HTTPException(409, "This ticket was just claimed by someone else.")
    if not _claim_eligible(t, ctx, is_su):
        raise HTTPException(403, "This ticket routes to a team you're not on — you can only claim requests your team handles.")
    t.assigned_agent_id = user.id
    _stamp_team_on_claim(t, ctx, user.id)
    note = (payload.note or "").strip()
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Agent",
                            action="assigned",
                            detail={"assigned_agent_id": str(user.id), "by": "claim", **({"note": note} if note else {})}))
    _dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                   title=f"Claimed: {t.subject}", action_url="/user/support/tickets/my")
    write_audit(db, entity_type="ticket", op="assigned", entity_id=t.id,
                actor_id=user.id, request=request,
                details={"assigned_agent_id": str(user.id), "by": "claim", **({"note": note} if note else {})})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────── Resolve / Close (worker) ───────────────────────
@router.post("/{ticket_id}/resolve", response_model=TicketResponse)
def resolve_my_ticket(
    ticket_id: UUID,
    payload: TicketResolve,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """A worker (agent / assignee / collaborator / team member / manager) resolves —
    optionally closes — the ticket with a structured ITIL resolution: code + root cause
    + summary + time. Stops the SLA clock and notifies the requester. Mirrors the admin
    resolve so non-superuser team members can close the loop from My Tickets."""
    if payload.resolution_code not in _RESOLUTION_CODES:
        raise HTTPException(422, f"Invalid resolution_code '{payload.resolution_code}'")
    if payload.resolution_category and payload.resolution_category not in _ROOT_CAUSES:
        raise HTTPException(422, f"Invalid resolution_category '{payload.resolution_category}'")
    _require_resolution_summary(payload.resolution_summary)
    t, _cw, _ = _load_visible(db, ticket_id, user, require_work=True)
    # Owner discipline (mirrors the agent-router resolve): closing the loop is the
    # assignee's / collaborator's / lead's / manager's act — a teammate must claim or be
    # handed the ticket first. And no owner ⇒ no resolution (assignment gate).
    _require_self_actor(db, t, user, "resolve it")
    if t.status not in TERMINAL_TICKET_STATUSES and not t.assigned_agent_id:
        raise HTTPException(409, "Assign an owner before resolving — nobody is working this ticket.")
    if t.status in TERMINAL_TICKET_STATUSES and not payload.close:
        raise HTTPException(409, "Ticket is already resolved or closed.")
    nowt = sla_util.now_utc()
    if t.first_responded_at is None:
        t.first_responded_at = nowt
    t.resolution_code = payload.resolution_code
    t.resolution_category = payload.resolution_category
    t.resolution_summary = payload.resolution_summary
    if payload.time_spent_minutes:
        t.time_spent_minutes = (t.time_spent_minutes or 0) + payload.time_spent_minutes
    if payload.note or payload.attachments:
        db.add(SdTicketComment(ticket_id=t.id, author_user_id=user.id,
                               author_name=getattr(user, "full_name", None) or "Support",
                               author_kind=CommentAuthorKind.STAFF.value,
                               body=payload.note or "Resolution evidence attached.",
                               is_internal=False, attachments=payload.attachments or []))
    target = TicketStatus.CLOSED.value if payload.close else TicketStatus.RESOLVED.value
    was_terminal = t.status in TERMINAL_TICKET_STATUSES
    sla_util.apply_pause_transition(t, t.status, target, nowt)   # credit paused time before stamping resolved_at
    t.status = target
    # Closing an ALREADY-RESOLVED ticket is just the resolved→closed step — keep the
    # original resolution stamps (re-stamping resolved_at skewed every TTR metric).
    if not was_terminal or t.resolved_at is None:
        t.resolved_at = nowt
        t.resolved_by_id = user.id
    if target == TicketStatus.CLOSED.value:
        t.closed_at = nowt
        t.closed_by_id = user.id
    sla_util.recompute_breach_flags(t, nowt)
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Support",
                            action="resolved", detail={"code": payload.resolution_code,
                                                        "category": payload.resolution_category, "closed": payload.close}))
    if t.raised_by_user_id and t.raised_by_user_id != user.id:
        _dispatch_safe(db, EVT_TICKET_RESOLVED, t.raised_by_user_id, t,
                       title=f"Ticket {t.ticket_number} {'closed' if payload.close else 'resolved'}",
                       action_url="/user/support/tickets/my")
    write_audit(db, entity_type="ticket", op="resolved", entity_id=t.id, actor_id=user.id,
                request=request, details={"code": payload.resolution_code, "closed": payload.close})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────── Collaborators (multiple people on a ticket) ───────────────────────
@router.post("/{ticket_id}/collaborators", response_model=TicketResponse)
def add_collaborator(
    ticket_id: UUID,
    payload: CollaboratorChange,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Add another person who can see + work this ticket (it surfaces under their own
    My Tickets). Owner-tier: the assignee / a collaborator / the lead curates the roster."""
    t, _cw, _ = _load_visible(db, ticket_id, user, require_work=True)
    _require_self_actor(db, t, user, "manage its collaborators")
    target = db.query(User).filter(User.id == payload.user_id, User.is_active == True).first()  # noqa: E712
    if not target:
        raise HTTPException(404, "User not found")
    uid = str(payload.user_id)
    collabs = [str(c) for c in (t.collaborators or [])]
    if uid == str(t.assigned_agent_id):
        raise HTTPException(409, "That person is already the assignee.")
    if uid not in collabs:
        collabs.append(uid)
        t.collaborators = collabs   # reassign → SQLAlchemy tracks the change
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                                actor_name=getattr(user, "full_name", None) or "Support",
                                action="collaborator_added", detail={"user_id": uid, "name": target.full_name}))
        _dispatch_safe(db, EVT_TICKET_ASSIGNED, payload.user_id, t,
                       title=f"You're now collaborating on {t.ticket_number}", action_url="/user/support/tickets/my")
        write_audit(db, entity_type="ticket", op="collaborator_added", entity_id=t.id,
                    actor_id=user.id, request=request, details={"user_id": uid})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.delete("/{ticket_id}/collaborators/{member_id}", response_model=TicketResponse)
def remove_collaborator(
    ticket_id: UUID,
    member_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Remove a collaborator. Owner-tier (assignee / collaborator / lead / manager)."""
    t, _cw, _ = _load_visible(db, ticket_id, user, require_work=True)
    _require_self_actor(db, t, user, "manage its collaborators")
    before = [str(c) for c in (t.collaborators or [])]
    after = [c for c in before if c != str(member_id)]
    if len(after) != len(before):
        t.collaborators = after
        db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                                actor_name=getattr(user, "full_name", None) or "Support",
                                action="collaborator_removed", detail={"user_id": str(member_id)}))
        write_audit(db, entity_type="ticket", op="collaborator_removed", entity_id=t.id,
                    actor_id=user.id, request=request, details={"user_id": str(member_id)})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))
