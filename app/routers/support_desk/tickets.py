"""Support Desk — admin/agent ticket router (the core).

CRUD + lifecycle (assign / status / escalate / resolve / reopen) + conversation
+ timeline, with SLA clocks, configurable numbering, audit and notifications.
All routes require a superadmin (admin panel). prefix=/support-desk/tickets.
"""
from __future__ import annotations

import csv
import io
import json
import secrets
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, case, func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketComment, SdTicketActivity, SdTicketReminder
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, TicketType, TicketSource, CommentAuthorKind,
    OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES, PRIORITY_ORDER, VENDOR_WAIT_REASONS,
    HOLD_REASON_CODES, ESCALATION_TYPES, ESCALATION_REASON_CODES,
    CHRONIC_REOPEN_THRESHOLD, ResolutionCode, RootCauseCategory,
    ARCHIVE_REASON_CODES, ArchiveReason, SUPPORT_ARCHIVE_RETENTION_DAYS,
    EVT_TICKET_CREATED, EVT_TICKET_ASSIGNED, EVT_TICKET_REPLIED,
    EVT_TICKET_STATUS, EVT_TICKET_ESCALATED, EVT_TICKET_RESOLVED, EVT_TICKET_MERGED,
    EVT_TICKET_REOPENED, EVT_TICKET_ARCHIVED, EVT_TICKET_RESTORED,
)
from app.schemas.support_desk.ticket import (
    TicketCreate, TicketUpdate, TicketResponse, TicketDetailResponse, TicketListResponse,
    TicketAssign, TicketStatusChange, TicketCsat, CommentCreate, CommentResponse, ActivityResponse,
    TicketBulkAction, TicketBulkResponse, TicketBulkResult,
    TicketRemind, TicketNudgeOwner, TicketRca, TicketMajorIncident, TicketHold, TicketReopen, TicketEscalate,
    TicketResume, TicketHoldExtend,
    TicketResolve, TicketMerge, TicketTimeLog, WorkbenchStats, CollaboratorChange,
    TicketFollowUpCreate, TicketKbPromote, TicketMergeChain, MergeChainNode,
    TicketVendorDispatch, TicketVendorChase, TicketVendorReply,
    TicketAck, TicketStatusUpdate, TicketPresenceResponse, TicketViewerInfo,
    TicketEscalationAck, TicketDeEscalate, EscalationEvent,
    TicketRestore, TicketLegalHold, CommentRedact, TicketChangeRequester,
)
from app.utils.dependencies import get_support_agent, get_current_superuser
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.utils.support_desk.workbench import compute_workbench
from app.utils.support_desk.assignment import route_and_assign, match_route, teams_handling
from app.utils.support_desk.rules import evaluate_rules, apply_default_queue
from app.routers.support_desk._common import (
    generate_ticket_number, resolve_sla_package, enrich_tickets, enrich_ticket, maybe_auto_close,
    auto_resume_expired_holds, apply_overdue_scope, apply_reopen, apply_close_source,
    require_resolution_summary,
)

router = APIRouter(prefix="/support-desk/tickets", tags=["Support Desk — Tickets"])

_PRIORITIES = {p.value for p in TicketPriority}
_TYPES = {t.value for t in TicketType}
_SOURCES = {s.value for s in TicketSource}
_STATUSES = {s.value for s in TicketStatus}
PORTAL_TOKEN_TTL_DAYS = 14


def _actor_name(user: User) -> str:
    return getattr(user, "full_name", None) or getattr(user, "email", None) or "Agent"


def _panel_base(db: Session, recipient_user_id) -> str:
    """The correct portal prefix for a notification recipient. Agents live on
    /user/support (the agent workspace); only superusers land on /admin/support-desk.
    Hardcoding /admin/... sent non-superuser agents to a panel their token can't open."""
    try:
        su = db.query(User.is_superuser).filter(User.id == recipient_user_id).scalar()
    except Exception:  # noqa: BLE001 — routing nicety, never fatal
        su = False
    return "/admin/support-desk" if su else "/user/support"


def _log_activity(db: Session, ticket: SdTicket, actor: User, action: str, detail: dict | None = None):
    # ``detail`` lands in a JSONB column — psycopg2 can't adapt Python UUID/datetime/Decimal
    # objects (e.g. payload.model_dump() carries raw UUIDs). Round-trip through json with a
    # str() fallback so every caller is JSON-safe regardless of what it passes.
    safe_detail = json.loads(json.dumps(detail or {}, default=str))
    db.add(SdTicketActivity(
        ticket_id=ticket.id, actor_user_id=actor.id if actor else None,
        actor_name=_actor_name(actor) if actor else "System",
        action=action, detail=safe_detail,
    ))


def _get_ticket(db: Session, ticket_id: UUID, admin: User | None = None) -> SdTicket:
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket not found")
    if admin is not None:
        _require_ticket_scope(db, t, admin)
    return t


def _require_ticket_scope(db: Session, t: SdTicket, admin: User) -> None:
    """Single-ticket mirror of the list team-seal. The list/stats surface was sealed via
    _agent_scope but every fetch-by-id route skipped it, so a non-superuser agent could
    read or mutate ANY ticket on the desk by UUID (the exact defect the bulk route
    documents having fixed for batches). 404 — not 403 — outside scope, so existence
    doesn't leak. Superusers pass untouched."""
    cond, _ctx = _agent_scope(db, admin)
    if cond is None:
        return
    if not db.query(SdTicket.id).filter(SdTicket.id == t.id, cond).first():
        raise HTTPException(404, "Ticket not found")


def _ticket_actor_error(t: SdTicket, admin: User, ctx: dict | None, db: Session | None = None) -> str | None:
    """Owner-tier check with a precomputed team context (ctx=None ⇒ superuser, always
    allowed). Returns a deny/skip reason, or None when the caller may command this ticket:
      • the ASSIGNED agent (it's their ticket),
      • a named COLLABORATOR (explicitly invited to work it),
      • the LEAD of the ticket's owning team (ServiceNow-style group manager),
      • anyone claim-eligible when the ticket is UNASSIGNED (triage pool — claiming /
        assigning formalizes ownership),
      • a participant of a LIVE swarm on this ticket (rights last only for the swarm — db
        required; passed by every real caller).
    A plain teammate viewing a colleague's assigned ticket gets a reason back."""
    if ctx is None:
        return None
    uid = str(admin.id)
    if t.assigned_agent_id and str(t.assigned_agent_id) == uid:
        return None
    if uid in [str(c) for c in (t.collaborators or [])]:
        return None
    from app.routers.support_desk.tickets_self import _is_lead
    if t.team_id and any(tm.id == t.team_id and _is_lead(tm, admin.id) for tm in ctx["teams"]):
        return None
    if not t.assigned_agent_id:
        from app.routers.support_desk.tickets_self import _claim_eligible
        if _claim_eligible(t, ctx, False):
            return None
    if db is not None:
        from app.routers.support_desk.tickets_self import _in_active_swarm
        if _in_active_swarm(db, t.id, admin.id):
            return None
    return "Assigned to another agent — only they, the team lead, or an admin can act on it."


def _require_ticket_actor(db: Session, t: SdTicket, admin: User, action: str = "act on it") -> None:
    """OWNER-tier workflow gate (ServiceNow/Zendesk discipline): status moves, escalation,
    hold/vendor lifecycle, resolution, reopen, merge and archive on an ASSIGNED ticket are
    reserved to the assignee, their collaborators, the team lead, or a superuser. Teammates
    keep read + comment + ack-nothing rights via the scope seal; the sanctioned transfer
    paths remain claim (unassigned) and handoff (own ticket, reason-coded)."""
    if getattr(admin, "is_superuser", False):
        return
    from app.routers.support_desk.tickets_self import _team_context
    if _ticket_actor_error(t, admin, _team_context(db, admin), db) is None:
        return
    raise HTTPException(
        403, f"This ticket is assigned to another agent — only they, the team lead, or an admin can {action}.")


def _transition_status(db: Session, t: SdTicket, new: str, actor: User, note: str | None = None,
                       reopen_source: str = "agent", reopen_reason_code: str | None = None) -> bool:
    """Apply a status transition with SLA / reopen bookkeeping + activity + dispatch.
    Shared by the single-ticket status route AND bulk. Returns True if it changed."""
    old = t.status
    if old == new:
        return False
    nowt = sla_util.now_utc()
    is_reopen = old in TERMINAL_TICKET_STATUSES and new in OPEN_TICKET_STATUSES
    # Stop-the-clock: freeze on entering a pause state / extend deadlines on leaving one.
    # MUST run before resolved_at is stamped below so a resolve-from-pause still credits the
    # paused time to the resolution deadline.
    sla_util.apply_pause_transition(t, old, new, nowt)
    if old == TicketStatus.OPEN.value and t.first_responded_at is None:
        t.first_responded_at = nowt
    if is_reopen:
        # Single-writer reopen engine: cycle record + failed-fix snapshot + fresh
        # re-resolution SLA clock + the 'reopened' activity (see _common.apply_reopen).
        apply_reopen(db, t, actor.id if actor else None,
                     _actor_name(actor) if actor else "System",
                     source=reopen_source, reason=note,
                     reason_code=reopen_reason_code, nowt=nowt)
    if new == TicketStatus.RESOLVED.value:
        t.resolved_at = nowt
        t.resolved_by_id = actor.id if actor else None
    elif new == TicketStatus.CLOSED.value:
        t.closed_at = nowt
        t.closed_by_id = actor.id if actor else None
        if t.resolved_at is None:
            t.resolved_at = nowt
            t.resolved_by_id = actor.id if actor else None
    # ── Vendor hand-off bookkeeping (Vendor Relay Station) ──
    # Entering PENDING_VENDOR stamps a fresh dispatch clock and re-arms the overdue sweep;
    # each hand-off is its own cycle, so we reset the reply marker too.
    if new == TicketStatus.PENDING_VENDOR.value:
        t.vendor_dispatched_at = nowt
        t.vendor_reply_at = None
        t.vendor_overdue_flagged = False
    elif old == TicketStatus.PENDING_VENDOR.value:
        # Leaving the hand-off — the vendor lane is no longer overdue-eligible.
        t.vendor_overdue_flagged = False
    # ── On-hold bookkeeping (Suspension Dock) ──
    # EVERY path into ON_HOLD (dedicated /hold route, set-status, bulk, board drop) must
    # stamp the hold context, and EVERY path out (resume, set-status, resolve, escalate)
    # must clear it — otherwise resume mis-defaults to in_progress / stale hold metadata
    # lingers on an active ticket. The `or` keeps /hold's richer payload stamps intact.
    if new == TicketStatus.ON_HOLD.value:
        t.held_from_status = t.held_from_status or old
        t.held_at = t.held_at or nowt
    elif old == TicketStatus.ON_HOLD.value:
        t.hold_reason = None
        t.hold_reason_code = None
        t.hold_until = None
        t.held_at = None
        t.held_from_status = None
        t.last_hold_review_at = None
        t.hold_review_count = 0
    t.status = new
    sla_util.recompute_breach_flags(t, nowt)
    _log_activity(db, t, actor, "status_changed", {"from": old, "to": new, "note": note})
    if t.raised_by_user_id:
        if new == TicketStatus.RESOLVED.value:
            evt, title = EVT_TICKET_RESOLVED, f"Ticket {t.ticket_number}: resolved"
        elif is_reopen:
            evt, title = EVT_TICKET_REOPENED, f"Ticket {t.ticket_number} reopened"
        else:
            evt, title = EVT_TICKET_STATUS, f"Ticket {t.ticket_number}: {new.replace('_', ' ')}"
        dispatch_safe(db, evt, t.raised_by_user_id, t, title=title,
                      action_url="/user/support/tickets")
    # A reopen also alerts the owning agent (they must re-earn the resolution) when the
    # actor isn't the owner themselves — e.g. a lead reopening on their behalf.
    if is_reopen and t.assigned_agent_id and (not actor or t.assigned_agent_id != actor.id):
        dispatch_safe(db, EVT_TICKET_REOPENED, t.assigned_agent_id, t,
                      title=f"Ticket {t.ticket_number} reopened — back on your desk",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/reopened")
    # Watchers (notify-only followers) hear about every status move through this single
    # writer — excluding the actor, the requester and the assignee (already pinged above).
    from app.utils.support_desk.watchers import notify_ticket_watchers
    w_evt = EVT_TICKET_RESOLVED if new == TicketStatus.RESOLVED.value else EVT_TICKET_STATUS
    notify_ticket_watchers(db, t, w_evt,
                           f"Ticket {t.ticket_number}: {new.replace('_', ' ')}",
                           actor_id=actor.id if actor else None,
                           exclude_ids=[t.raised_by_user_id, t.assigned_agent_id])
    return True


def _do_escalate(db: Session, t: SdTicket, actor: User, reason: str | None = None, *,
                 reason_code: str | None = None, escalation_type: str | None = None,
                 to_team_id=None, response_minutes: int | None = None,
                 update_interval_minutes: int | None = None) -> bool:
    """Raise a tier via the shared escalation engine (structured record + activity +
    esc-ACK reset + response clock), post the internal note, optionally arm the
    stakeholder update cadence, and notify the owner. Shared by route + bulk."""
    from app.utils.support_desk.escalation import apply_escalation
    apply_escalation(db, t, actor.id if actor else None,
                     _actor_name(actor) if actor else "System",
                     reason=reason, reason_code=reason_code,
                     escalation_type=escalation_type, to_team_id=to_team_id,
                     response_minutes=response_minutes)
    if reason:
        db.add(SdTicketComment(
            ticket_id=t.id, author_user_id=actor.id if actor else None,
            author_name=_actor_name(actor) if actor else "System",
            author_kind=CommentAuthorKind.STAFF.value,
            body=f"[Escalation] {reason}", is_internal=True))
    if update_interval_minutes:
        # Reuse the war-room cadence pair — the update-overdue sweep + Critical board
        # already service these; a parallel escalation-only timer would double all of it.
        nowt = sla_util.now_utc()
        t.update_interval_minutes = update_interval_minutes
        t.next_update_due_at = nowt + timedelta(minutes=update_interval_minutes)
    if t.assigned_agent_id:
        dispatch_safe(db, EVT_TICKET_ESCALATED, t.assigned_agent_id, t,
                      title=f"Escalated (L{t.escalation_level}): {t.subject}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/escalated?ticket={t.id}")
    return True


def _do_de_escalate(db: Session, t: SdTicket, actor: User, reason: str) -> None:
    """Stand an escalation down one level (shared by route + bulk). At level 0 the live
    escalation state is cleared (ack/clock/target/type) while the historical trail
    (escalated_at / reason / reason_code / auto_escalated_at) is KEPT — the UI gates the
    history on is_escalated. The de-escalation reason goes to the comment + activity;
    escalation_reason stays the escalate-time record."""
    t.escalation_level = max(0, (t.escalation_level or 0) - 1)
    if t.escalation_level == 0:
        t.is_escalated = False
        t.escalation_acknowledged_at = None
        t.escalation_acknowledged_by_id = None
        t.escalation_response_due_at = None
        t.escalated_to_team_id = None
        t.escalation_type = None
        if t.status == TicketStatus.ESCALATED.value:
            # Owner keeps working it; an ownerless one returns to the open queue —
            # IN_PROGRESS without an owner would violate the assignment gate.
            t.status = (TicketStatus.IN_PROGRESS.value if t.assigned_agent_id
                        else TicketStatus.OPEN.value)
    db.add(SdTicketComment(
        ticket_id=t.id, author_user_id=actor.id if actor else None,
        author_name=_actor_name(actor) if actor else "System",
        author_kind=CommentAuthorKind.STAFF.value,
        body=f"[De-escalation] {reason}", is_internal=True))
    _log_activity(db, t, actor, "de_escalated", {"level": t.escalation_level, "reason": reason})
    if t.assigned_agent_id:
        dispatch_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id, t,
                      title=f"De-escalated (now L{t.escalation_level}): {t.subject}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/escalated?ticket={t.id}")


# ─────────────────────── Status-workflow guards (corporate / ITIL) ───────────────────────
# A ticket needs an OWNER before it can enter "being worked" or "closed" states — you can't
# resolve / close / start work on something nobody is assigned to. Mirrors the bulk modal's
# client-side eligibility so the UI and the server agree.
_ASSIGNED_REQUIRED_STATUSES = {
    TicketStatus.IN_PROGRESS.value, TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value,
}
# Statuses a plain "set status" action may target (work states only). Resolve/close go through
# the resolve flow (captures a resolution code); escalate has its own action; leaving a terminal
# state is a reopen.
_WORK_STATUSES = {
    TicketStatus.OPEN.value, TicketStatus.IN_PROGRESS.value,
    TicketStatus.PENDING_CUSTOMER.value, TicketStatus.PENDING_VENDOR.value,
    TicketStatus.ON_HOLD.value,
}


def _assignment_gate_error(t: SdTicket, new: str) -> str | None:
    """Universal rule: no owner ⇒ can't move into work/closure. Returns a message or None."""
    if new in _ASSIGNED_REQUIRED_STATUSES and not t.assigned_agent_id:
        return "Assign an owner before moving this ticket into progress or closing it."
    return None


def _set_status_guard(t: SdTicket, new: str) -> str | None:
    """Full guard for the generic 'set status' action — returns a skip reason or None."""
    if new == t.status:
        return f"Already {new.replace('_', ' ')}"
    if new in TERMINAL_TICKET_STATUSES:
        return "Use Resolve to resolve or close a ticket."
    if new == TicketStatus.ESCALATED.value:
        return "Use Escalate to escalate a ticket."
    if t.status in TERMINAL_TICKET_STATUSES:
        return "Reopen this resolved/closed ticket before changing its status."
    return _assignment_gate_error(t, new)


# Whitelisted sort columns (anything else falls back to created_at) — never trust a raw column name.
_SORT_COLUMNS = {
    "created_at": SdTicket.created_at,
    "updated_at": SdTicket.updated_at,
    "ticket_number": SdTicket.ticket_number,
    "subject": SdTicket.subject,
    "status": SdTicket.status,
    "resolution_due_at": SdTicket.resolution_due_at,
    "response_due_at": SdTicket.response_due_at,
    "reopened_count": SdTicket.reopened_count,
    # Reopened desk: latest cycle first.
    "last_reopened_at": SdTicket.last_reopened_at,
    "escalation_level": SdTicket.escalation_level,
    # Breached desk: oldest-breach-first == sort by breach stamp asc (overage descends).
    "sla_resolution_breached_at": SdTicket.sla_resolution_breached_at,
    "sla_response_breached_at": SdTicket.sla_response_breached_at,
    # Resolved desk: newest-resolution-first, rating, and time-to-resolve (seconds).
    "resolved_at": SdTicket.resolved_at,
    "closed_at": SdTicket.closed_at,
    "csat_score": SdTicket.csat_score,
    "ttr": func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at),
    # Archived desk: newest-into-storage first.
    "archived_at": SdTicket.archived_at,
}
# priority is a string column; order it by severity, not alphabetically.
_PRIORITY_RANK = case(
    {p: i for i, p in enumerate(PRIORITY_ORDER)}, value=SdTicket.priority, else_=-1
)


def _apply_scope(query, scope: str, admin: User):
    """Translate a UI scope into a filter. Returns the filtered query."""
    if scope == "my":
        return query.filter(SdTicket.assigned_agent_id == admin.id)
    if scope == "unassigned":
        return query.filter(SdTicket.assigned_agent_id.is_(None),
                            SdTicket.status.in_(OPEN_TICKET_STATUSES))
    if scope == "critical":
        return query.filter(SdTicket.priority == TicketPriority.CRITICAL.value)
    if scope == "escalated":
        return query.filter(SdTicket.is_escalated == True)  # noqa: E712
    if scope in ("pending", "pending_customer", "pending_vendor"):
        if scope == "pending_customer":
            return query.filter(SdTicket.status == TicketStatus.PENDING_CUSTOMER.value)
        if scope == "pending_vendor":
            return query.filter(SdTicket.status == TicketStatus.PENDING_VENDOR.value)
        return query.filter(SdTicket.status.in_([TicketStatus.PENDING_CUSTOMER.value,
                                                TicketStatus.PENDING_VENDOR.value]))
    if scope in ("in_progress", "open"):
        # the "Open / In Progress" desk = everything actively being worked
        return query.filter(SdTicket.status.in_([TicketStatus.OPEN.value,
                                                TicketStatus.IN_PROGRESS.value]))
    if scope == "on_hold":
        return query.filter(SdTicket.status == TicketStatus.ON_HOLD.value)
    if scope == "sla_breached":
        return query.filter(or_(SdTicket.sla_response_breached == True,
                                SdTicket.sla_resolution_breached == True))  # noqa: E712
    if scope == "due_soon":
        # The Breached desk's "prevent the NEXT breach" rail: clock running, resolution
        # not yet missed, due inside the at-risk window. Server-side so pagination holds.
        nowt = sla_util.now_utc()
        return query.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES),
                            SdTicket.sla_paused_since.is_(None),
                            SdTicket.resolved_at.is_(None),
                            SdTicket.sla_resolution_breached == False,  # noqa: E712
                            SdTicket.resolution_due_at.isnot(None),
                            SdTicket.resolution_due_at > nowt,
                            SdTicket.resolution_due_at <= nowt + timedelta(hours=2))
    if scope == "overdue":
        # Paused tickets are excluded — their clock is frozen, so a stale raw due-date while
        # on hold / awaiting a reply is not "overdue" (the deadline is pushed out on resume).
        return query.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES),
                            SdTicket.sla_paused_since.is_(None),
                            SdTicket.resolution_due_at.isnot(None),
                            SdTicket.resolution_due_at < sla_util.now_utc())
    if scope == "reopened":
        return query.filter(SdTicket.reopened_count > 0)
    if scope == "resolved":
        return query.filter(SdTicket.status == TicketStatus.RESOLVED.value)
    if scope == "closed":
        return query.filter(SdTicket.status == TicketStatus.CLOSED.value)
    return query


def _agent_scope(db: Session, admin: User):
    """Team-scope condition for a NON-superuser support agent (returns (None, None) for
    superusers). Closes the desk-wide loophole on the agent list/calendar/export: an agent
    only sees their teams' tickets + the triage pool routing to them + their own involvement.
    Mirrors `/me/tickets/command-center` (single source of truth in tickets_self)."""
    if getattr(admin, "is_superuser", False):
        return None, None
    from app.routers.support_desk.tickets_self import _team_context, _command_center_filter
    ctx = _team_context(db, admin)
    return _command_center_filter(admin, ctx), ctx


# ─────────────────────────────── List ───────────────────────────────
@router.get("/", response_model=TicketListResponse)
def list_tickets(
    scope: Optional[str] = Query(None, description="all|my|unassigned|open|in_progress|pending|pending_customer|pending_vendor|on_hold|critical|escalated|sla_breached|due_soon|overdue|reopened|resolved|closed|archived"),
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    ticket_type: Optional[str] = None,
    organization_id: Optional[UUID] = None,
    assigned_agent_id: Optional[UUID] = None,
    support_team: Optional[str] = None,
    team_id: Optional[UUID] = None,
    queue_id: Optional[UUID] = None,
    tag: Optional[str] = None,
    created_from: Optional[datetime] = None,
    created_to: Optional[datetime] = None,
    include_deleted: bool = False,
    mine: bool = Query(False, description="Personal-desk lens: only tickets ASSIGNED TO ME. The user-portal status desks send this so an agent's Critical/Escalated/Overdue/… boards show their own load — team-wide slices stay on Team / All / Unassigned."),
    include_major: bool = Query(False, description="With scope=critical: widen to priority=critical OR is_major_incident (a major incident may run at any priority)"),
    breach_kind: Optional[str] = Query(None, description="With scope=sla_breached: response|resolution|both"),
    overdue_kind: Optional[str] = Query(None, description="With scope=overdue: any|response|resolution (default resolution — legacy). 'response' = past the response target with no first reply; 'any' = either clock."),
    missing_rca: Optional[bool] = Query(None, description="With scope=sla_breached: only tickets lacking BOTH breach_reason and rca_summary"),
    active_only: Optional[bool] = Query(None, description="Exclude terminal (resolved/closed) tickets — the Breached desk's default working set"),
    reopen_source: Optional[str] = Query(None, description="Reopened desk: requester|agent|portal|auto — who kicked the ticket back"),
    chronic: Optional[bool] = Query(None, description="Reopened desk: only repeat offenders (reopened_count >= chronic threshold)"),
    reopened_from: Optional[datetime] = Query(None, description="Reopened desk: last_reopened_at >= this instant"),
    reopened_to: Optional[datetime] = Query(None, description="Reopened desk: last_reopened_at <= this instant"),
    resolved_from: Optional[datetime] = Query(None, description="Resolved desk: resolved_at >= this instant"),
    resolved_to: Optional[datetime] = Query(None, description="Resolved desk: resolved_at <= this instant"),
    resolution_code: Optional[str] = Query(None, description="Resolved desk: filter by resolution code"),
    resolution_category: Optional[str] = Query(None, description="Resolved desk: filter by root cause"),
    resolved_by: Optional[UUID] = Query(None, description="Resolved desk: who recorded the fix (falls back to the assignee for legacy rows)"),
    csat: Optional[str] = Query(None, description="Resolved desk: rated|unrated|low (low = score <= 2)"),
    pending_close: Optional[bool] = Query(None, description="Resolved desk: only tickets still on the pre-close shelf (status=resolved)"),
    include_closed: Optional[bool] = Query(None, description="With scope=resolved: widen to resolved+closed (the full terminal set)"),
    closed_from: Optional[datetime] = Query(None, description="Closed desk: closed_at >= this instant"),
    closed_to: Optional[datetime] = Query(None, description="Closed desk: closed_at <= this instant"),
    close_source: Optional[str] = Query(None, description="Closed desk: auto_sweep|manual|merged|withdrawn|no_response — how the record was sealed"),
    follow_up_of: Optional[UUID] = Query(None, description="Closed desk: children of this ticket (the follow-up chain)"),
    archived_from: Optional[datetime] = Query(None, description="Archived desk: archived_at >= this instant"),
    archived_to: Optional[datetime] = Query(None, description="Archived desk: archived_at <= this instant"),
    archive_reason_code: Optional[str] = Query(None, description="Archived desk: coded taxonomy (spam|duplicate|...|auto_retention) or 'uncoded' for legacy rows"),
    legal_hold: Optional[bool] = Query(None, description="Archived desk: only records under (or not under) legal hold"),
    purge_eligible: Optional[bool] = Query(None, description="Archived desk: only records past the retention window (never legal-held)"),
    q: Optional[str] = None,
    sort_by: str = Query("created_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    # Archived view (and explicit include_deleted) is the only place tombstoned rows surface.
    archived = scope == "archived"
    query = db.query(SdTicket)
    if archived:
        query = query.filter(SdTicket.is_deleted == True)  # noqa: E712
    elif not include_deleted:
        query = query.filter(SdTicket.is_deleted == False)  # noqa: E712

    # Hard team-seal for non-superusers (un-bypassable): an agent never enumerates the
    # whole desk. Out-of-scope team_id/assigned_agent_id params are dropped, not honoured.
    _cc_cond, _cc_ctx = _agent_scope(db, admin)
    if _cc_cond is not None:
        query = query.filter(_cc_cond)
        if team_id and team_id not in _cc_ctx["team_ids"]:
            team_id = None
        if assigned_agent_id and assigned_agent_id not in (_cc_ctx["member_ids"] | _cc_ctx["reports"] | {admin.id}):
            assigned_agent_id = None
        if resolved_by and resolved_by not in (_cc_ctx["member_ids"] | _cc_ctx["reports"] | {admin.id}):
            resolved_by = None

    # Personal-desk lens: composes with every scope (critical+mine, archived+mine, …).
    if mine:
        query = query.filter(SdTicket.assigned_agent_id == admin.id)

    if scope == "critical" and include_major:
        # War-room widening: a major incident may run at ANY priority — the critical board
        # must never hide one. The bare scope stays a pure priority filter for other callers.
        query = query.filter(or_(SdTicket.priority == TicketPriority.CRITICAL.value,
                                 SdTicket.is_major_incident == True))  # noqa: E712
    elif scope == "overdue":
        # Overdue recovery desk — `overdue_kind` widens to the response clock (any|response);
        # default stays the legacy resolution-only semantics for existing callers.
        query = apply_overdue_scope(query, overdue_kind)
    elif scope == "resolved" and include_closed:
        # Resolved desk "Include closed" toggle: widen to the full terminal set (the desk
        # normalizes back down with an explicit status=resolved when the toggle is off).
        query = query.filter(SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)))
    elif scope and scope not in ("all", "archived"):
        query = _apply_scope(query, scope, admin)

    # Update-cadence sweep: opening the Critical board nudges owners whose promised
    # stakeholder update has lapsed (once daily per ticket; cron covers unattended desks).
    if scope == "critical":
        try:
            from app.routers.support_desk._common import sweep_update_overdue
            if sweep_update_overdue(db):
                db.commit()
        except Exception:
            db.rollback()

    # Vendor OLA sweep: when an agent opens the Pending Vendor queue, flag + escalate any
    # hand-offs that have blown past their expected-return date (idempotent, team-scoped).
    if scope in ("pending_vendor", "pending"):
        try:
            from app.utils.support_desk.vendor import sweep_vendor_overdue
            if sweep_vendor_overdue(db, team_cond=_cc_cond):
                db.commit()
        except Exception:
            db.rollback()

    # Hold-expiry sweep: opening the On-Hold dock releases any hold whose hold_until has
    # passed (auto-resume; SLA un-freezes). Idempotent — the cron covers unattended desks.
    if scope == "on_hold":
        try:
            auto_resume_expired_holds(db)
        except Exception:
            db.rollback()

    # Auto-close sweep: opening the Resolved desk (or the Closed archive) closes any
    # ticket whose 3-day reopen window has lapsed, so the pre-close shelf AND the
    # archive are honest the moment they render (idempotent; commits itself; the cron
    # covers unattended desks).
    if scope in ("resolved", "closed"):
        try:
            from app.routers.support_desk._common import auto_close_due_tickets
            auto_close_due_tickets(db)
        except Exception:
            db.rollback()

    # Retention sweep: opening the Archived desk (or the Closed archive) moves CLOSED
    # records older than the auto-archive window into deep storage (auto_retention),
    # so both desks are honest the moment they render. Legal-hold rows are exempt.
    # Idempotent; commits itself; the cron covers unattended desks.
    if scope in ("archived", "closed"):
        try:
            from app.routers.support_desk._common import auto_archive_old_closed
            auto_archive_old_closed(db)
        except Exception:
            db.rollback()

    # Breach-flag sweep: opening the Breached desk (or the live Overdue lens) flips the
    # stored breach flags for idle tickets whose deadline silently passed — the stored
    # flags are otherwise only refreshed on write paths, so an untouched ticket would
    # never surface here. Stamps sla_*_breached_at + timeline activity + owner ping.
    if scope in ("sla_breached", "overdue", "due_soon"):
        try:
            from app.utils.support_desk.breach import sweep_sla_breach_flags
            if sweep_sla_breach_flags(db, team_cond=_cc_cond):
                db.commit()
        except Exception:
            db.rollback()

    # Escalation sweeps: opening the Escalated desk (a) auto-escalates SLA-resolution-
    # breached, owned, actively-worked tickets EXACTLY ONCE (stamped) and (b) nudges owners
    # whose escalation sat unacknowledged past its response deadline (day-throttled).
    if scope == "escalated":
        try:
            from app.utils.support_desk.escalation import (
                sweep_sla_breach_escalation, sweep_escalation_response_overdue)
            if sweep_sla_breach_escalation(db, team_cond=_cc_cond):
                db.commit()
            sweep_escalation_response_overdue(db)   # commits itself when it nudged
        except Exception:
            db.rollback()

    # Breached-desk refinements (server-side so pagination/count stay correct).
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
    # Reopened-desk refinements (server-side so pagination/count stay correct).
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
    # Resolved-desk refinements (server-side so pagination/count stay correct).
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
    # Closed-desk refinements (server-side so pagination/count stay correct).
    if closed_from:
        query = query.filter(SdTicket.closed_at.isnot(None),
                             SdTicket.closed_at >= closed_from)
    if closed_to:
        query = query.filter(SdTicket.closed_at.isnot(None),
                             SdTicket.closed_at <= closed_to)
    query = apply_close_source(query, close_source)
    if follow_up_of:
        query = query.filter(SdTicket.follow_up_of_id == follow_up_of)
    # Archived-desk refinements (server-side so pagination/count stay correct).
    if archived_from:
        query = query.filter(SdTicket.archived_at.isnot(None),
                             SdTicket.archived_at >= archived_from)
    if archived_to:
        query = query.filter(SdTicket.archived_at.isnot(None),
                             SdTicket.archived_at <= archived_to)
    if archive_reason_code:
        if archive_reason_code == "uncoded":
            # legacy tombstones archived before the taxonomy existed
            query = query.filter(SdTicket.is_deleted == True,  # noqa: E712
                                 SdTicket.archive_reason_code.is_(None))
        else:
            query = query.filter(SdTicket.archive_reason_code == archive_reason_code)
    if legal_hold is not None:
        query = query.filter(SdTicket.legal_hold == legal_hold)
    if purge_eligible:
        _cutoff = sla_util.now_utc() - timedelta(days=SUPPORT_ARCHIVE_RETENTION_DAYS)
        query = query.filter(SdTicket.is_deleted == True,   # noqa: E712
                             SdTicket.legal_hold == False,  # noqa: E712
                             SdTicket.archived_at.isnot(None),
                             SdTicket.archived_at < _cutoff)

    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if ticket_type:
        query = query.filter(SdTicket.ticket_type == ticket_type)
    if organization_id:
        query = query.filter(SdTicket.organization_id == organization_id)
    if assigned_agent_id:
        query = query.filter(SdTicket.assigned_agent_id == assigned_agent_id)
    if support_team:
        query = query.filter(SdTicket.support_team == support_team)
    if team_id:
        query = query.filter(SdTicket.team_id == team_id)
    if queue_id:
        query = query.filter(SdTicket.queue_id == queue_id)
    if tag:
        query = query.filter(SdTicket.tags.contains([tag]))
    if created_from:
        query = query.filter(SdTicket.created_at >= created_from)
    if created_to:
        query = query.filter(SdTicket.created_at <= created_to)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))

    total = query.count()

    sort_expr = _PRIORITY_RANK if sort_by == "priority" else _SORT_COLUMNS.get(sort_by, SdTicket.created_at)
    sort_expr = sort_expr.asc() if (sort_dir or "desc").lower() == "asc" else sort_expr.desc()
    items = query.order_by(sort_expr, SdTicket.created_at.desc()).offset((page - 1) * limit).limit(limit).all()

    enrich_tickets(db, items)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in items],
        total=total, page=page, limit=limit,
    )


# ─────────────────────────────── Calendar feed ───────────────────────────────
# NOTE: declared BEFORE GET /{ticket_id} so the static path wins the route match.
@router.get("/calendar")
def ticket_calendar(
    dt_from: Optional[datetime] = Query(None, alias="from"),
    dt_to: Optional[datetime] = Query(None, alias="to"),
    scope: Optional[str] = None,
    team_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Tickets with a resolution-due date in [from, to] → calendar events."""
    query = db.query(SdTicket).filter(SdTicket.is_deleted == False)  # noqa: E712
    _cc_cond, _cc_ctx = _agent_scope(db, admin)
    if _cc_cond is not None:
        query = query.filter(_cc_cond)
        if team_id and team_id not in _cc_ctx["team_ids"]:
            team_id = None
    if scope and scope not in ("all", "archived"):
        query = _apply_scope(query, scope, admin)
    if team_id:
        query = query.filter(SdTicket.team_id == team_id)
    query = query.filter(SdTicket.resolution_due_at.isnot(None))
    if dt_from:
        query = query.filter(SdTicket.resolution_due_at >= dt_from)
    if dt_to:
        query = query.filter(SdTicket.resolution_due_at <= dt_to)
    items = query.order_by(SdTicket.resolution_due_at).limit(500).all()
    return [{
        "id": str(t.id),
        "ticket_number": t.ticket_number,
        "subject": t.subject,
        "priority": t.priority,
        "status": t.status,
        "due_at": t.resolution_due_at.isoformat() if t.resolution_due_at else None,
        "is_breached": bool(t.sla_resolution_breached),
        "kind": "resolution",
    } for t in items]


# ─────────────────────────────── CSV export ───────────────────────────────
@router.get("/export")
def export_tickets(
    scope: Optional[str] = None,
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    ticket_type: Optional[str] = None,
    organization_id: Optional[UUID] = None,
    assigned_agent_id: Optional[UUID] = None,
    team_id: Optional[UUID] = None,
    queue_id: Optional[UUID] = None,
    closed_from: Optional[datetime] = Query(None, description="Closed desk: closed_at >= this instant"),
    closed_to: Optional[datetime] = Query(None, description="Closed desk: closed_at <= this instant"),
    close_source: Optional[str] = Query(None, description="Closed desk: auto_sweep|manual|merged|withdrawn|no_response"),
    archived_from: Optional[datetime] = Query(None, description="Archived desk: archived_at >= this instant"),
    archived_to: Optional[datetime] = Query(None, description="Archived desk: archived_at <= this instant"),
    archive_reason_code: Optional[str] = Query(None, description="Archived desk: coded taxonomy or 'uncoded'"),
    legal_hold: Optional[bool] = Query(None, description="Archived desk: legal-hold filter"),
    purge_eligible: Optional[bool] = Query(None, description="Archived desk: only records past the retention window"),
    q: Optional[str] = None,
    mine: bool = Query(False, description="Personal-desk lens: only tickets assigned to me (matches the user-portal desks)"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    archived = scope == "archived"
    query = db.query(SdTicket)
    query = query.filter(SdTicket.is_deleted == (True if archived else False))  # noqa: E712
    _cc_cond, _cc_ctx = _agent_scope(db, admin)
    if _cc_cond is not None:
        query = query.filter(_cc_cond)
        if team_id and team_id not in _cc_ctx["team_ids"]:
            team_id = None
        if assigned_agent_id and assigned_agent_id not in (_cc_ctx["member_ids"] | _cc_ctx["reports"] | {admin.id}):
            assigned_agent_id = None
    if mine:
        query = query.filter(SdTicket.assigned_agent_id == admin.id)
    if scope and scope not in ("all", "archived"):
        query = _apply_scope(query, scope, admin)
    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if ticket_type:
        query = query.filter(SdTicket.ticket_type == ticket_type)
    if organization_id:
        query = query.filter(SdTicket.organization_id == organization_id)
    if assigned_agent_id:
        query = query.filter(SdTicket.assigned_agent_id == assigned_agent_id)
    if team_id:
        query = query.filter(SdTicket.team_id == team_id)
    if queue_id:
        query = query.filter(SdTicket.queue_id == queue_id)
    # Closed-desk refinements — the export must ship exactly what the page shows.
    if closed_from:
        query = query.filter(SdTicket.closed_at.isnot(None),
                             SdTicket.closed_at >= closed_from)
    if closed_to:
        query = query.filter(SdTicket.closed_at.isnot(None),
                             SdTicket.closed_at <= closed_to)
    query = apply_close_source(query, close_source)
    # Archived-desk refinements — the export must ship exactly what the page shows.
    if archived_from:
        query = query.filter(SdTicket.archived_at.isnot(None), SdTicket.archived_at >= archived_from)
    if archived_to:
        query = query.filter(SdTicket.archived_at.isnot(None), SdTicket.archived_at <= archived_to)
    if archive_reason_code:
        if archive_reason_code == "uncoded":
            query = query.filter(SdTicket.is_deleted == True,  # noqa: E712
                                 SdTicket.archive_reason_code.is_(None))
        else:
            query = query.filter(SdTicket.archive_reason_code == archive_reason_code)
    if legal_hold is not None:
        query = query.filter(SdTicket.legal_hold == legal_hold)
    if purge_eligible:
        _cutoff = sla_util.now_utc() - timedelta(days=SUPPORT_ARCHIVE_RETENTION_DAYS)
        query = query.filter(SdTicket.is_deleted == True,   # noqa: E712
                             SdTicket.legal_hold == False,  # noqa: E712
                             SdTicket.archived_at.isnot(None), SdTicket.archived_at < _cutoff)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))
    items = query.order_by(SdTicket.created_at.desc()).limit(5000).all()
    enrich_tickets(db, items)

    def _close_source_of(t) -> str:
        if t.status != TicketStatus.CLOSED.value:
            return ""
        if t.merged_into_id:
            return "merged"
        if t.resolution_code == ResolutionCode.CANCELLED.value:
            return "withdrawn"
        if t.resolution_code == ResolutionCode.NO_RESPONSE.value:
            return "no_response"
        return "manual" if t.closed_by_id else "auto_sweep"

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Ticket", "Subject", "Type", "Priority", "Status", "Organization",
                "Requester", "Agent", "Created", "Resolution due", "SLA breached", "Reopened",
                "Closed at", "Closed by", "Close source", "Resolution code", "CSAT",
                "Archived at", "Archived by", "Archive reason", "Legal hold"])
    for t in items:
        w.writerow([
            t.ticket_number, t.subject, t.ticket_type, t.priority, t.status,
            getattr(t, "organization_name", "") or "",
            getattr(t, "raised_by_name", "") or getattr(t, "contact_name", "") or "",
            getattr(t, "assigned_agent_name", "") or "",
            t.created_at.isoformat() if t.created_at else "",
            t.resolution_due_at.isoformat() if t.resolution_due_at else "",
            "yes" if t.sla_resolution_breached else "no",
            t.reopened_count or 0,
            t.closed_at.isoformat() if t.closed_at else "",
            getattr(t, "closed_by_name", "") or ("System" if t.status == TicketStatus.CLOSED.value and not t.closed_by_id else ""),
            _close_source_of(t),
            t.resolution_code or "",
            t.csat_score if t.csat_score is not None else "",
            t.archived_at.isoformat() if getattr(t, "archived_at", None) else "",
            getattr(t, "archived_by_name", "") or ("System" if t.is_deleted and not t.archived_by_id else ""),
            (t.archive_reason_code or ("uncoded" if t.is_deleted else "")),
            "yes" if getattr(t, "legal_hold", False) else ("no" if t.is_deleted else ""),
        ])
    buf.seek(0)
    fname = f"tickets-{scope or 'all'}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ─────────────────────────────── Workbench (agent: assigned-to-me) ───────────────────────────────
# Declared BEFORE GET /{ticket_id} so the literal path wins (a UUID converter would
# 422 on "workbench", but order keeps it unambiguous — same as /calendar + /export).
@router.get("/workbench", response_model=WorkbenchStats)
def my_workbench(db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False, SdTicket.assigned_agent_id == admin.id)  # noqa: E712
    return compute_workbench(db, base, actor=admin)


@router.get("/vendor-scorecard")
def vendor_scorecard(db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Per-vendor turnaround deck: for each vendor the desk has handed work to, how many are
    open now, how many are overdue, and the average completed round-trip. Team-scoped for
    non-superusers (same hard seal as the ticket list). Declared before GET /{ticket_id}."""
    q = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.vendor_name.isnot(None), SdTicket.vendor_name != "")
    cond, _ = _agent_scope(db, admin)
    if cond is not None:
        q = q.filter(cond)
    rows = q.limit(3000).all()
    now = sla_util.now_utc()
    agg: dict[str, dict] = {}
    for t in rows:
        name = (t.vendor_name or "").strip()
        if not name:
            continue
        a = agg.setdefault(name, {"vendor_name": name, "open": 0, "overdue": 0,
                                  "handoffs": 0, "_turn_days": [], "last_dispatched_at": None})
        disp = sla_util._aware(getattr(t, "vendor_dispatched_at", None))
        reply = sla_util._aware(getattr(t, "vendor_reply_at", None))
        due = sla_util._aware(getattr(t, "vendor_due_at", None))
        if disp:
            a["handoffs"] += 1
            if a["last_dispatched_at"] is None or disp > a["last_dispatched_at"]:
                a["last_dispatched_at"] = disp
        if disp and reply and reply >= disp:
            a["_turn_days"].append((reply - disp).total_seconds() / 86400.0)
        if t.status == TicketStatus.PENDING_VENDOR.value:
            a["open"] += 1
            if due and now > due:
                a["overdue"] += 1
    out = []
    for a in agg.values():
        turns = a.pop("_turn_days")
        a["avg_turnaround_days"] = round(sum(turns) / len(turns), 1) if turns else None
        a["last_dispatched_at"] = a["last_dispatched_at"].isoformat() if a["last_dispatched_at"] else None
        out.append(a)
    out.sort(key=lambda x: (x["open"], x["overdue"], x["handoffs"]), reverse=True)
    return {"vendors": out, "total_vendors": len(out)}


# ─────────────────────────────── Create ───────────────────────────────
@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    if payload.priority not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{payload.priority}'")
    if payload.ticket_type not in _TYPES:
        raise HTTPException(422, f"Invalid ticket_type '{payload.ticket_type}'")
    if payload.source not in _SOURCES:
        raise HTTPException(422, f"Invalid source '{payload.source}'")

    # Team-routing guard (the rule): only a superuser, a reporting manager, or a
    # member/lead of the chosen team may route a ticket to a SPECIFIC team. A plain
    # agent who isn't on that team has team_id dropped → auto-routing decides instead.
    team_id = payload.team_id
    if team_id and not getattr(admin, "is_superuser", False):
        from app.routers.support_desk.tickets_self import _team_context
        ctx = _team_context(db, admin)
        if not (bool(ctx["reports"]) or team_id in ctx["team_ids"]):
            team_id = None

    # Queue-routing guard (same rule, lane flavour): only a superuser, a reporting
    # manager, or a member/lead of the lane's OWNING team may pin a ticket to a
    # SPECIFIC queue. route_and_assign honours an explicit queue and stamps that
    # lane's team — so an ungated queue_id would sidestep the team guard above.
    # A dropped/unknown/retired lane falls through to auto-routing instead.
    queue_id = payload.queue_id
    if queue_id and not getattr(admin, "is_superuser", False):
        from app.models.support_desk.workspace import SdQueue
        from app.routers.support_desk.tickets_self import _team_context
        _qrow = db.query(SdQueue).filter(
            SdQueue.id == queue_id, SdQueue.is_deleted == False,  # noqa: E712
            SdQueue.is_active == True).first()  # noqa: E712
        if _qrow is None:
            queue_id = None
        else:
            _qctx = _team_context(db, admin)
            _qtids = {str(x) for x in _qctx["team_ids"]}
            if not (bool(_qctx["reports"])
                    or (_qrow.team_id and str(_qrow.team_id) in _qtids)):
                queue_id = None

    # Reporting-manager assignee guard (the rule): a manager who is NOT a superuser may
    # name a specific assignee only if that agent is on the team this ticket routes to
    # (explicit team override, else the type/category match). Plain agents and admins are
    # unaffected. Mirrors the create page so it can't be bypassed via the API.
    if (payload.assigned_agent_id and payload.assigned_agent_id != admin.id
            and not getattr(admin, "is_superuser", False)):
        from app.routers.support_desk.tickets_self import _team_context, _team_members_of
        _ctx = _team_context(db, admin)
        if _ctx["reports"]:  # a reporting manager
            # Explicit override → that team's roster; else → union of EVERY team that
            # handles this type/category (so multiple owning teams aren't reduced to one).
            if team_id:
                _allowed, _has_team = _team_members_of(db, team_id), True
            else:
                _allowed = set()
                _handling = teams_handling(db, payload.ticket_type, payload.category_id)
                for _tm in _handling:
                    _allowed |= _team_members_of(db, _tm.id)
                _has_team = bool(_handling)
            if _has_team and payload.assigned_agent_id not in _allowed:
                raise HTTPException(422, "The agent you assigned isn't on a team that handles this request type. Pick an agent who is.")

    # Self gate (the rule): a plain agent — not a superuser, not a reporting manager —
    # may raise only the request types their own teams own, checked against the union
    # of EVERY owning team. An EMPTY union blocks too (a type no team has claimed must
    # not slip into triage). Mirrors the create page's step-2 gate + create_my_ticket
    # so this desk endpoint can't be used to sidestep it.
    if not getattr(admin, "is_superuser", False):
        from app.routers.support_desk.tickets_self import _team_context, _team_members_of
        _sctx = _team_context(db, admin)
        if not _sctx["reports"]:  # not a reporting manager
            _sunion = set()
            _shandling = teams_handling(db, payload.ticket_type, payload.category_id)
            for _tm in _shandling:
                _sunion |= _team_members_of(db, _tm.id)
            if admin.id not in _sunion:
                if _shandling:
                    raise HTTPException(403, "You're not on a team that handles this request type — pick a request type your team handles, or ask one of the owning teams to raise it.")
                raise HTTPException(403, f"No support team handles '{payload.ticket_type}' requests yet — pick a different request type, or ask an admin to set up routing for it.")
            # Assignee reach (the rule): a plain agent names an assignee only from the
            # owning teams' rosters (or themselves). Managers/admins are gated above —
            # without this the create path out-reached /assign, letting a plain agent
            # park a ticket on ANY user in the company.
            if (payload.assigned_agent_id and payload.assigned_agent_id != admin.id
                    and payload.assigned_agent_id not in _sunion):
                raise HTTPException(422, "The agent you assigned isn't on a team that handles this request type. Pick an agent who is.")

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
        source=payload.source,
        template_id=template_id,
        status=TicketStatus.OPEN.value,
        # Creator = submitter (ServiceNow "opened by"). Without this stamp an agent's
        # own ticket had NO requester link — if auto-routing assigned it elsewhere it
        # vanished from every view the creator can see ("created but not created").
        # The contact_*/customer fields keep carrying the CLIENT the ticket is for.
        raised_by_user_id=admin.id,
        is_internal=not payload.organization_id,
        organization_id=payload.organization_id,
        customer_id=payload.customer_id,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        contact_phone=payload.contact_phone,
        department=payload.department,
        location=payload.location,
        support_team=payload.support_team,
        assigned_agent_id=payload.assigned_agent_id,
        assigned_engineer_id=payload.assigned_engineer_id,
        assigned_pm_id=payload.assigned_pm_id,
        team_id=team_id,
        queue_id=queue_id,
        collaborators=[str(c) for c in (payload.collaborators or [])],
        business_impact=payload.business_impact,
        affected_users=payload.affected_users,
        revenue_impact=payload.revenue_impact,
        vendor_name=payload.vendor_name,
        linked_change_id=payload.linked_change_id,
        linked_problem_id=payload.linked_problem_id,
        links=payload.links,
        sla_package_id=pkg.id if pkg else payload.sla_package_id,
        response_due_at=rd,
        resolution_due_at=rsd,
        attachments=payload.attachments or [],
        tags=payload.tags or [],
        created_by_id=admin.id,
    )
    db.add(t)
    db.flush()  # assign id
    _log_activity(db, t, admin, "created", {"ticket_number": number, "priority": payload.priority})
    # Routing chain (first-match): admin-authored automation rules → category/type
    # router → default-queue fallback → (if the queue auto-assigns) pick an agent.
    # Skips assign if the agent already chose an assignee. Best-effort — never blocks.
    evaluate_rules(db, t)
    route_and_assign(db, t)
    apply_default_queue(db, t)
    if t.assigned_agent_id:
        dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                      title=f"Assigned: {t.subject}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/my?ticket={t.id}")
    write_audit(db, entity_type="ticket", op="created", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"ticket_number": number, "subject": t.subject, "priority": t.priority})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Detail ───────────────────────────────
@router.get("/{ticket_id}", response_model=TicketDetailResponse)
def get_ticket(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    # Raw fetch: archived (soft-deleted) records stay READABLE — the Deep Storage desk
    # inspects a tombstone before deciding to restore or purge it. Mutation routes keep
    # the _get_ticket 404 semantics, so a tombstone is read-only outside restore/purge.
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    _require_ticket_scope(db, t, admin)
    if not t.is_deleted:
        maybe_auto_close(db, t)   # lazily close if the reopen window has elapsed
    enrich_ticket(db, t)
    return TicketDetailResponse.model_validate(t)


# ─────────────────────────────── Update ───────────────────────────────
@router.patch("/{ticket_id}", response_model=TicketResponse)
def update_ticket(
    ticket_id: UUID,
    payload: TicketUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "edit it")
    # Closed = sealed record (ServiceNow/Zendesk discipline): the sanctioned continuations
    # are reopen and follow-up. A superuser keeps the audited correction path; everyone
    # else must not quietly rewrite a record the requester believes is final.
    if t.status == TicketStatus.CLOSED.value and not getattr(admin, "is_superuser", False):
        raise HTTPException(409, "This ticket is closed — reopen it or open a follow-up instead of editing the sealed record.")
    update = payload.model_dump(exclude_unset=True)
    if "priority" in update and update["priority"] not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{update['priority']}'")
    if "ticket_type" in update and update["ticket_type"] not in _TYPES:
        raise HTTPException(422, f"Invalid ticket_type '{update['ticket_type']}'")
    # Team/queue re-routing mirrors /assign's rule (lead of the ticket's team or a
    # superuser) — without this, PATCH was a side door around the reassignment
    # discipline the dedicated route enforces.
    moves_team = ("team_id" in update and update["team_id"] != t.team_id) or \
                 ("queue_id" in update and update["queue_id"] != t.queue_id)
    if moves_team and not getattr(admin, "is_superuser", False):
        from app.routers.support_desk.tickets_self import _team_context, _is_lead
        _ctx = _team_context(db, admin)
        is_lead_here = bool(t.team_id) and any(
            tm.id == t.team_id and _is_lead(tm, admin.id) for tm in _ctx["teams"])
        if not is_lead_here:
            raise HTTPException(403, "Only the team lead or an admin can move a ticket between teams or queues.")

    changed = {}
    for k, v in update.items():
        old = getattr(t, k, None)
        if old != v:
            changed[k] = {"from": str(old), "to": str(v)}
            setattr(t, k, v)

    # Re-arm SLA clocks if priority/package changed before first response.
    if ("priority" in update or "sla_package_id" in update) and t.first_responded_at is None:
        pkg = resolve_sla_package(db, t.sla_package_id, t.organization_id)
        rd, rsd = sla_util.compute_deadlines(pkg, t.priority, start=t.created_at)
        t.sla_package_id = pkg.id if pkg else t.sla_package_id
        t.response_due_at, t.resolution_due_at = rd, rsd

    if changed:
        _log_activity(db, t, admin, "updated", {"changes": changed})
        write_audit(db, entity_type="ticket", op="updated", entity_id=t.id,
                    actor_id=admin.id, request=request, details={"changes": changed})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Assign ───────────────────────────────
@router.post("/{ticket_id}/assign", response_model=TicketResponse)
def assign_ticket(
    ticket_id: UUID,
    payload: TicketAssign,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id, admin)
    # Reassignment discipline: an agent may claim/route an UNASSIGNED ticket or re-route
    # their OWN; poaching a teammate's assigned ticket is a lead/superuser act. The
    # sanctioned peer path stays /me/tickets/{id}/handoff (reason-coded, assignee-driven).
    _require_ticket_actor(db, t, admin, "reassign it")
    data = payload.model_dump(exclude_unset=True)
    if not getattr(admin, "is_superuser", False):
        # A non-superuser routes within their reach: the ticket's owning team, teams they
        # lead, their direct reports, or themselves. Team/queue re-routing stays a
        # lead/superuser act (functional moves go through Escalate, which records why).
        from app.routers.support_desk.tickets_self import _team_context, _team_members_of, _is_lead
        ctx = _team_context(db, admin)
        is_lead_here = bool(t.team_id) and any(
            tm.id == t.team_id and _is_lead(tm, admin.id) for tm in ctx["teams"])
        if ("team_id" in data or "queue_id" in data) and not is_lead_here:
            raise HTTPException(403, "Only the team lead or an admin can move a ticket between teams or queues.")
        target = data.get("assigned_agent_id")
        if target:
            pool = (_team_members_of(db, t.team_id) if t.team_id else ctx["member_ids"])
            pool = pool | ctx["led_member_ids"] | ctx["reports"] | {admin.id}
            if target not in pool:
                raise HTTPException(403, "You can only assign to yourself or a member of the ticket's team.")
    prev_agent = t.assigned_agent_id
    for k, v in data.items():
        setattr(t, k, v)
    _log_activity(db, t, admin, "assigned", data)
    if t.assigned_agent_id and t.assigned_agent_id != prev_agent:
        dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                      title=f"Assigned: {t.subject}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/my?ticket={t.id}")
    write_audit(db, entity_type="ticket", op="assigned", entity_id=t.id,
                actor_id=admin.id, request=request, details=data)
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────── Collaborators ───────────────────────────
@router.post("/{ticket_id}/collaborators", response_model=TicketResponse)
def add_ticket_collaborator(ticket_id: UUID, payload: CollaboratorChange, request: Request,
                            db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Add a person who can see + work this ticket (surfaces under their My Tickets)."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "manage its collaborators")
    target = db.query(User).filter(User.id == payload.user_id, User.is_active == True).first()  # noqa: E712
    if not target:
        raise HTTPException(404, "User not found")
    uid = str(payload.user_id)
    if uid == str(t.assigned_agent_id):
        raise HTTPException(409, "That person is already the assignee.")
    collabs = [str(c) for c in (t.collaborators or [])]
    if uid not in collabs:
        collabs.append(uid)
        t.collaborators = collabs
        _log_activity(db, t, admin, "collaborator_added", {"user_id": uid, "name": target.full_name})
        dispatch_safe(db, EVT_TICKET_ASSIGNED, payload.user_id, t,
                      title=f"You're now collaborating on {t.ticket_number}", action_url="/user/support/tickets/my")
        write_audit(db, entity_type="ticket", op="collaborator_added", entity_id=t.id,
                    actor_id=admin.id, request=request, details={"user_id": uid})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.delete("/{ticket_id}/collaborators/{member_id}", response_model=TicketResponse)
def remove_ticket_collaborator(ticket_id: UUID, member_id: UUID, request: Request,
                               db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "manage its collaborators")
    before = [str(c) for c in (t.collaborators or [])]
    after = [c for c in before if c != str(member_id)]
    if len(after) != len(before):
        t.collaborators = after
        _log_activity(db, t, admin, "collaborator_removed", {"user_id": str(member_id)})
        write_audit(db, entity_type="ticket", op="collaborator_removed", entity_id=t.id,
                    actor_id=admin.id, request=request, details={"user_id": str(member_id)})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Status ───────────────────────────────
@router.post("/{ticket_id}/status", response_model=TicketResponse)
def change_status(
    ticket_id: UUID,
    payload: TicketStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    new = payload.status
    if new not in _STATUSES:
        raise HTTPException(422, f"Invalid status '{new}'")
    # Terminal targets must go through /resolve (which captures the resolution record) —
    # a bare set-status into resolved/closed was the last path that could mint an
    # uncoded, summary-less resolution. Mirrors the bulk set_status _WORK_STATUSES rule.
    if new in TERMINAL_TICKET_STATUSES:
        raise HTTPException(422, "Use Resolve to resolve or close a ticket — it records the resolution.")
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "change its status")
    old = t.status
    if old == new:
        raise HTTPException(409, f"Ticket is already '{new}'")
    gate = _assignment_gate_error(t, new)
    if gate:
        raise HTTPException(409, gate)
    _transition_status(db, t, new, admin, payload.note)
    write_audit(db, entity_type="ticket", op="status_changed", entity_id=t.id,
                actor_id=admin.id, request=request, details={"from": old, "to": new})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Escalate ───────────────────────────────
@router.post("/{ticket_id}/escalate", response_model=TicketResponse)
def escalate_ticket(
    ticket_id: UUID,
    request: Request,
    payload: TicketEscalate | None = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "escalate it")
    # Same assignment-before-action discipline as work/closure transitions: a ticket nobody
    # owns can't be raised a tier — assign an owner first (the escalate console does this in
    # one step via its "Assign owner" field). Mirrored in the bulk-escalate guard.
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Reopen this resolved/closed ticket before escalating it.")
    if not t.assigned_agent_id:
        raise HTTPException(409, "Assign an owner before escalating — a ticket nobody is working can't be raised a tier.")
    reason = (payload.reason if payload else None) or None
    reason_code = (payload.reason_code if payload else None) or None
    esc_type = (payload.escalation_type if payload else None) or None
    if reason_code and reason_code not in ESCALATION_REASON_CODES:
        raise HTTPException(422, f"Invalid reason_code '{reason_code}'")
    if esc_type and esc_type not in ESCALATION_TYPES:
        raise HTTPException(422, f"Invalid escalation_type '{esc_type}'")
    # Functional escalation: route to a REAL team (FK) and/or the legacy free-text field.
    to_team_id = None
    if payload and payload.team_id:
        from app.models.support_desk.workspace import SdTeam
        team = db.query(SdTeam).filter(SdTeam.id == payload.team_id,
                                       SdTeam.is_deleted == False).first()  # noqa: E712
        if not team:
            raise HTTPException(404, "Target team not found")
        to_team_id = team.id
        t.team_id = team.id           # the batch actually moves to the receiving team
        # Re-park the LANE to match (tier boards are queue-scoped): moving the team
        # while leaving queue_id behind strands the ticket on the OLD team's board
        # and the receiving team never sees it there. Their own lane if they have
        # one, else no lane — the team's ticket desks still carry it.
        from app.utils.support_desk.assignment import _find_queue_for_team
        _lane = _find_queue_for_team(db, team.id)
        _new_qid = _lane.id if _lane else None
        if str(t.queue_id or "") != str(_new_qid or ""):
            db.add(SdTicketActivity(
                ticket_id=t.id, actor_user_id=None, actor_name="Routing",
                action="routed",
                detail={"queue": _lane.name if _lane else None, "by": "escalation"}))
            t.queue_id = _new_qid
        if not esc_type:
            esc_type = "functional"   # routing to a team IS a functional escalation
    if payload and payload.support_team:
        t.support_team = payload.support_team
    _do_escalate(db, t, admin, reason=reason, reason_code=reason_code,
                 escalation_type=esc_type, to_team_id=to_team_id,
                 response_minutes=(payload.response_minutes if payload else None),
                 update_interval_minutes=(payload.update_interval_minutes if payload else None))
    write_audit(db, entity_type="ticket", op="escalated", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"level": t.escalation_level, "reason": reason,
                         "reason_code": reason_code, "type": t.escalation_type,
                         "to_team_id": str(to_team_id) if to_team_id else None,
                         "response_due_at": (t.escalation_response_due_at.isoformat()
                                             if t.escalation_response_due_at else None)})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────── Phase 2 — specialized actions ───────────────────────
@router.post("/{ticket_id}/de-escalate", response_model=TicketResponse)
def de_escalate(ticket_id: UUID, payload: TicketDeEscalate, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Stand an escalation down one level. A reason is REQUIRED (422 via the schema) —
    de-escalation is a judgement call that must be defensible on the timeline. Notifies
    the owner (the old silent path was a gap)."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "de-escalate it")
    if (t.escalation_level or 0) <= 0 and not t.is_escalated:
        raise HTTPException(409, "Ticket is not escalated")
    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(422, "A reason is required to de-escalate.")
    _do_de_escalate(db, t, admin, reason)
    write_audit(db, entity_type="ticket", op="de_escalated", entity_id=t.id, actor_id=admin.id,
                request=request, details={"level": t.escalation_level, "reason": reason})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.get("/{ticket_id}/escalation-history", response_model=list[EscalationEvent])
def escalation_history(ticket_id: UUID, db: Session = Depends(get_db),
                       admin: User = Depends(get_support_agent)):
    """The ticket's tier timeline — every escalate/de-escalate rung with actor, reason,
    direction, target team and DWELL (time spent at that level). Derived from the
    immutable activity log (no separate table): apply_escalation writes the structured
    detail, so history is complete for every path incl. the auto-sweeps."""
    t = _get_ticket(db, ticket_id, admin)
    rows = (db.query(SdTicketActivity)
            .filter(SdTicketActivity.ticket_id == t.id,
                    SdTicketActivity.action.in_(["escalated", "de_escalated"]))
            .order_by(SdTicketActivity.created_at.asc()).all())
    if not rows:
        return []
    team_ids = {r.detail.get("to_team_id") for r in rows
                if isinstance(r.detail, dict) and r.detail.get("to_team_id")}
    team_names = {}
    if team_ids:
        from app.models.support_desk.workspace import SdTeam
        ids = [i for i in ({_as_uuid_safe(x) for x in team_ids}) if i]
        team_names = {str(tm.id): tm.name for tm in
                      db.query(SdTeam.id, SdTeam.name).filter(SdTeam.id.in_(ids)).all()}
    nowt = sla_util.now_utc()
    events: list[EscalationEvent] = []
    for i, r in enumerate(rows):
        d = r.detail if isinstance(r.detail, dict) else {}
        at = sla_util._aware(r.created_at)
        if i + 1 < len(rows):
            nxt = sla_util._aware(rows[i + 1].created_at)
            dwell = max(0, int((nxt - at).total_seconds() * 1000))
        else:
            # The standing rung keeps accruing while the ticket is still escalated.
            dwell = max(0, int((nowt - at).total_seconds() * 1000)) if t.is_escalated else None
        events.append(EscalationEvent(
            at=r.created_at, action=r.action,
            level=int(d.get("level") or 0),
            actor_id=r.actor_user_id, actor_name=r.actor_name,
            reason=d.get("reason"), reason_code=d.get("reason_code"),
            escalation_type=d.get("escalation_type") or d.get("type"),
            to_team_id=_as_uuid_safe(d.get("to_team_id")),
            to_team_name=team_names.get(str(d.get("to_team_id"))) if d.get("to_team_id") else None,
            auto=bool(d.get("auto")), dwell_ms=dwell,
        ))
    return events


def _as_uuid_safe(v):
    try:
        return v if isinstance(v, UUID) else (UUID(str(v)) if v else None)
    except Exception:  # noqa: BLE001
        return None


@router.post("/{ticket_id}/remind", response_model=TicketResponse)
def remind(ticket_id: UUID, payload: TicketRemind, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "send the requester a reminder")
    # Nothing is awaited on a resolved/closed ticket — don't nudge the requester about a done item.
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — there's nothing awaiting the requester.")
    t.reminder_count = (t.reminder_count or 0) + 1
    t.last_reminder_at = sla_util.now_utc()
    _log_activity(db, t, admin, "reminded", {"count": t.reminder_count, "message": payload.message})
    if t.raised_by_user_id:
        dispatch_safe(db, EVT_TICKET_STATUS, t.raised_by_user_id, t,
                      title=f"Reminder — {t.ticket_number} awaits your reply", action_url="/user/support/tickets")
    write_audit(db, entity_type="ticket", op="reminded", entity_id=t.id, actor_id=admin.id, request=request, details={"count": t.reminder_count})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/nudge-owner", response_model=TicketResponse)
def nudge_owner(ticket_id: UUID, request: Request, payload: TicketNudgeOwner | None = None,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Overdue-desk accountability ping: nudge the ASSIGNED AGENT of a late ticket (the
    /remind route above nudges the REQUESTER — different audience). Team-sealed for
    non-superuser agents and day-throttled per ticket (dedupe on the last `owner_nudge`
    timeline entry, mirroring sweep_update_overdue) so a desk lead can't spam an owner."""
    t = _get_ticket(db, ticket_id, admin)
    cond, _ = _agent_scope(db, admin)
    if cond is not None and not db.query(SdTicket.id).filter(SdTicket.id == t.id, cond).first():
        raise HTTPException(404, "Ticket not found")   # out of the caller's team scope
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — nothing left to chase.")
    if not t.assigned_agent_id:
        raise HTTPException(409, "This ticket has no owner — assign it instead of nudging.")
    if t.assigned_agent_id == admin.id:
        raise HTTPException(409, "You own this ticket — the nudge would go to you.")
    nowt = sla_util.now_utc()
    recent = (db.query(SdTicketActivity.id)
              .filter(SdTicketActivity.ticket_id == t.id,
                      SdTicketActivity.action == "owner_nudge",
                      SdTicketActivity.created_at > nowt - timedelta(days=1)).first())
    if recent:
        raise HTTPException(409, "The owner was already nudged in the last 24 hours.")
    due = sla_util._aware(t.resolution_due_at) or sla_util._aware(t.response_due_at)
    late_min = max(0, int((nowt - due).total_seconds() // 60)) if due else 0
    msg = (payload.message if payload else None) or None
    _log_activity(db, t, admin, "owner_nudge", {"message": msg, "late_minutes": late_min})
    late_lbl = (f"{late_min // 1440}d {(late_min % 1440) // 60}h" if late_min >= 1440
                else f"{late_min // 60}h {late_min % 60}m" if late_min >= 60 else f"{late_min}m")
    dispatch_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id, t,
                  title=f"Nudge from {_actor_name(admin)} — {t.ticket_number} is {late_lbl} past target",
                  action_url="/user/support/tickets/overdue")
    write_audit(db, entity_type="ticket", op="owner_nudged", entity_id=t.id, actor_id=admin.id,
                request=request, details={"late_minutes": late_min})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────── Vendor Relay Station (pending-vendor lifecycle) ───────────────────────────
def _apply_vendor_fields(t: SdTicket, payload) -> list[str]:
    """Copy the vendor memo/lifecycle fields off a payload onto the ticket. Returns changed keys."""
    changed = []
    for k in ("vendor_name", "vendor_ticket_ref", "vendor_wait_reason", "vendor_due_at", "vendor_po_ref", "vendor_status"):
        v = getattr(payload, k, None)
        if v is not None:
            if k == "vendor_wait_reason" and v not in VENDOR_WAIT_REASONS:
                raise HTTPException(422, f"Invalid vendor_wait_reason '{v}'")
            setattr(t, k, v)
            changed.append(k)
    eng = getattr(payload, "assigned_engineer_id", None)
    if eng is not None:
        t.assigned_engineer_id = eng
        changed.append("assigned_engineer_id")
    return changed


@router.post("/{ticket_id}/vendor-dispatch", response_model=TicketResponse)
def vendor_dispatch(ticket_id: UUID, payload: TicketVendorDispatch, request: Request,
                    db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Hand a ticket off to a third-party vendor: record who/why/ETA and move it into
    PENDING_VENDOR (auto-pauses the customer SLA via _transition_status)."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "dispatch it to a vendor")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — reopen it before dispatching to a vendor.")
    changed = _apply_vendor_fields(t, payload)
    # Transition INTO pending_vendor (idempotent — no-op if already there but still records the memo edits).
    moved = _transition_status(db, t, TicketStatus.PENDING_VENDOR.value, admin, note=payload.note)
    _log_activity(db, t, admin, "vendor_dispatched", {
        "vendor": t.vendor_name, "ref": t.vendor_ticket_ref, "reason": t.vendor_wait_reason,
        "due_at": t.vendor_due_at, "moved": moved, "note": payload.note, "fields": changed,
    })
    write_audit(db, entity_type="ticket", op="vendor_dispatch", entity_id=t.id, actor_id=admin.id,
                request=request, details={"vendor": t.vendor_name, "moved": moved})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/vendor-chase", response_model=TicketResponse)
def vendor_chase(ticket_id: UUID, payload: TicketVendorChase, request: Request,
                 db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Follow up WITH THE VENDOR — internal tracking only (never notifies the client)."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "chase the vendor")
    if t.status != TicketStatus.PENDING_VENDOR.value:
        raise HTTPException(409, "This ticket isn't waiting on a vendor — nothing to chase.")
    t.vendor_reminder_count = (t.vendor_reminder_count or 0) + 1
    t.last_vendor_reminder_at = sla_util.now_utc()
    if payload.vendor_due_at is not None:
        t.vendor_due_at = payload.vendor_due_at
        t.vendor_overdue_flagged = False  # re-arm the overdue sweep on a fresh ETA
    _log_activity(db, t, admin, "vendor_chased", {"count": t.vendor_reminder_count, "message": payload.message})
    # Optional courtesy nudge to the internal vendor coordinator (engineer) — not the customer.
    if t.assigned_engineer_id:
        dispatch_safe(db, EVT_TICKET_STATUS, t.assigned_engineer_id, t,
                      title=f"Chase vendor — {t.ticket_number} ({t.vendor_name or 'vendor'})",
                      action_url="/user/support/tickets/pending-vendor")
    write_audit(db, entity_type="ticket", op="vendor_chase", entity_id=t.id, actor_id=admin.id,
                request=request, details={"count": t.vendor_reminder_count})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/vendor-reply", response_model=TicketResponse)
def vendor_reply(ticket_id: UUID, payload: TicketVendorReply, request: Request,
                 db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Log a vendor's response as an INTERNAL comment (hidden from the client portal) and,
    by default, bring the ticket back to IN_PROGRESS (resumes the customer SLA)."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "log a vendor reply")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(422, "A vendor reply needs a body.")
    db.add(SdTicketComment(
        ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
        author_kind=CommentAuthorKind.VENDOR.value, body=body,
        is_internal=True, attachments=payload.attachments or [],
    ))
    t.vendor_reply_at = sla_util.now_utc()
    if payload.vendor_status is not None:
        t.vendor_status = payload.vendor_status
    _log_activity(db, t, admin, "vendor_replied", {"resume": payload.resume, "chars": len(body)})
    if payload.resume and t.status == TicketStatus.PENDING_VENDOR.value:
        _transition_status(db, t, TicketStatus.IN_PROGRESS.value, admin, note="vendor replied")
    write_audit(db, entity_type="ticket", op="vendor_reply", entity_id=t.id, actor_id=admin.id,
                request=request, details={"resume": payload.resume})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/rca", response_model=TicketResponse)
def set_rca(ticket_id: UUID, payload: TicketRca, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "record its RCA")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(t, k, v)
    _log_activity(db, t, admin, "rca_recorded", {"fields": list(data.keys())})
    write_audit(db, entity_type="ticket", op="rca", entity_id=t.id, actor_id=admin.id, request=request, details={"fields": list(data.keys())})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/major-incident", response_model=TicketResponse)
def major_incident(ticket_id: UUID, payload: TicketMajorIncident, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "change its major-incident state")
    # A major incident is an ACTIVE-disruption op (war room, desk-wide visibility). You can't
    # DECLARE one on a resolved/closed ticket — there's nothing live to coordinate. Reopen it
    # first if the issue recurred; use RCA for the historical record. (Editing an already-major
    # incident's impact fields post-resolution stays allowed for the post-incident review.)
    if payload.is_major_incident and not t.is_major_incident and t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Reopen this resolved/closed ticket before declaring a major incident — there's no active disruption to escalate.")
    t.is_major_incident = payload.is_major_incident
    for k in ("business_impact", "affected_users", "revenue_impact", "war_room_url"):
        v = getattr(payload, k)
        if v is not None:
            setattr(t, k, v)
    # Optionally arm the stakeholder update cadence on declare. Stand-down leaves the
    # cadence untouched — the agent stops it explicitly via /status-update stop_cadence.
    if payload.is_major_incident and payload.update_interval_minutes:
        t.update_interval_minutes = payload.update_interval_minutes
        t.next_update_due_at = sla_util.now_utc() + timedelta(minutes=payload.update_interval_minutes)
    _log_activity(db, t, admin, "major_incident", {"on": payload.is_major_incident, "impact": payload.business_impact,
                                                   "cadence_min": payload.update_interval_minutes})
    if payload.is_major_incident and t.assigned_agent_id:
        dispatch_safe(db, EVT_TICKET_ESCALATED, t.assigned_agent_id, t,
                      title=f"MAJOR INCIDENT — {t.subject}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/critical?ticket={t.id}")
    write_audit(db, entity_type="ticket", op="major_incident", entity_id=t.id, actor_id=admin.id, request=request, details={"on": payload.is_major_incident})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────── War Room (ack + status updates + presence) ───────────────────────────
@router.post("/{ticket_id}/ack", response_model=TicketResponse)
def ack_ticket(ticket_id: UUID, request: Request, payload: TicketAck | None = None,
               db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Acknowledge a critical — 'a responder owns eyes on this'. Stamps acknowledged_at/by
    (the MTTA source). Deliberately does NOT touch first_responded_at or the SLA clocks:
    ACK is an internal responder signal, not a customer-facing reply."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "acknowledge it")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — nothing to acknowledge.")
    if t.acknowledged_at:
        raise HTTPException(409, "Already acknowledged.")
    t.acknowledged_at = sla_util.now_utc()
    t.acknowledged_by_id = admin.id
    note = (payload.note.strip() if payload and payload.note and payload.note.strip() else None)
    _log_activity(db, t, admin, "acknowledged", {"note": note} if note else {})
    if note:
        db.add(SdTicketComment(
            ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
            author_kind=CommentAuthorKind.STAFF.value, body=f"[Ack] {note}", is_internal=True))
    if t.raised_by_user_id and t.raised_by_user_id != admin.id:
        dispatch_safe(db, EVT_TICKET_STATUS, t.raised_by_user_id, t,
                      title=f"A responder is on {t.ticket_number}", action_url="/user/support/tickets")
    write_audit(db, entity_type="ticket", op="acknowledged", entity_id=t.id,
                actor_id=admin.id, request=request, details={"note": note})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/escalation-ack", response_model=TicketResponse)
def escalation_ack(ticket_id: UUID, request: Request, payload: TicketEscalationAck | None = None,
                   db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Acknowledge an ESCALATION — 'the receiving tier owns eyes on this'. Stamps
    escalation_acknowledged_at/by (the eMTTA source). Distinct from the war-room /ack
    (incident MTTA) and from first_responded_at (customer-facing reply). The response
    deadline is deliberately kept (historical record) — overdue is computed as
    unacked AND past-due, so acking clears the overdue state by definition."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "acknowledge the escalation")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — nothing to acknowledge.")
    if not t.is_escalated:
        raise HTTPException(409, "Ticket is not escalated.")
    if t.escalation_acknowledged_at:
        raise HTTPException(409, "Escalation already acknowledged.")
    t.escalation_acknowledged_at = sla_util.now_utc()
    t.escalation_acknowledged_by_id = admin.id
    note = (payload.note.strip() if payload and payload.note and payload.note.strip() else None)
    _log_activity(db, t, admin, "escalation_acknowledged",
                  {"level": t.escalation_level, **({"note": note} if note else {})})
    if note:
        db.add(SdTicketComment(
            ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
            author_kind=CommentAuthorKind.STAFF.value,
            body=f"[Escalation ack] {note}", is_internal=True))
    if t.assigned_agent_id and t.assigned_agent_id != admin.id:
        dispatch_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id, t,
                      title=f"Escalation acknowledged (L{t.escalation_level}) — {t.ticket_number}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/escalated?ticket={t.id}")
    write_audit(db, entity_type="ticket", op="escalation_ack", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"level": t.escalation_level, "note": note})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/status-update", response_model=TicketResponse)
def post_status_update(ticket_id: UUID, payload: TicketStatusUpdate, request: Request,
                       db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Post a stakeholder status update (war-room comms). Lands as a ticket comment —
    internal work-note or public reply — and re-arms / stands down the update-cadence
    timer (next_update_due_at) that the update-overdue sweep watches."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "post a stakeholder update")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is resolved/closed — post a reopen or RCA instead.")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(422, "A status update needs a body.")
    db.add(SdTicketComment(
        ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
        author_kind=CommentAuthorKind.STAFF.value, body=body,
        is_internal=payload.is_internal,
    ))
    nowt = sla_util.now_utc()
    # A PUBLIC status update is a real staff reply — it stops the response clock and
    # notifies the requester (mirrors add_comment).
    if not payload.is_internal:
        if t.first_responded_at is None:
            t.first_responded_at = nowt
            sla_util.recompute_breach_flags(t, nowt)
        if t.raised_by_user_id:
            dispatch_safe(db, EVT_TICKET_REPLIED, t.raised_by_user_id, t,
                          title=f"Status update on {t.ticket_number}", action_url="/user/support/tickets")
    # Cadence bookkeeping.
    t.last_status_update_at = nowt
    if payload.stop_cadence:
        t.update_interval_minutes = None
        t.next_update_due_at = None
    else:
        iv = payload.interval_minutes or t.update_interval_minutes
        if iv:
            t.update_interval_minutes = iv
            t.next_update_due_at = nowt + timedelta(minutes=iv)
    _log_activity(db, t, admin, "status_update", {
        "internal": payload.is_internal, "chars": len(body),
        "interval_min": t.update_interval_minutes,
        "next_due": t.next_update_due_at.isoformat() if t.next_update_due_at else None,
        "stopped": payload.stop_cadence,
    })
    write_audit(db, entity_type="ticket", op="status_update", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"internal": payload.is_internal, "stopped": payload.stop_cadence})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# Presence heartbeats older than this are pruned; viewers seen within PRESENCE_LIVE_SECONDS
# count as "here right now". Frontend heartbeats every ~25s while a drawer is open.
PRESENCE_PRUNE_SECONDS = 120
PRESENCE_LIVE_SECONDS = 60


@router.post("/{ticket_id}/presence", response_model=TicketPresenceResponse)
def ticket_presence(ticket_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Agent-collision presence (Zendesk-style): upsert my heartbeat on this ticket and
    return everyone with it open right now. Cheap by design — one upsert + one select;
    stale rows are pruned opportunistically so the table stays bounded."""
    from app.models.support_desk.workspace import SdTicketViewer
    t = _get_ticket(db, ticket_id, admin)
    nowt = sla_util.now_utc()
    row = (db.query(SdTicketViewer)
           .filter(SdTicketViewer.ticket_id == t.id, SdTicketViewer.user_id == admin.id).first())
    if row:
        row.last_seen_at = nowt
    else:
        db.add(SdTicketViewer(ticket_id=t.id, user_id=admin.id, last_seen_at=nowt))
    # Opportunistic prune (whole table — it only ever holds currently-open drawers).
    db.query(SdTicketViewer).filter(
        SdTicketViewer.last_seen_at < nowt - timedelta(seconds=PRESENCE_PRUNE_SECONDS)
    ).delete(synchronize_session=False)
    db.commit()
    live = (db.query(SdTicketViewer)
            .filter(SdTicketViewer.ticket_id == t.id,
                    SdTicketViewer.last_seen_at >= nowt - timedelta(seconds=PRESENCE_LIVE_SECONDS))
            .all())
    names = {u.id: (u.full_name or u.email) for u in
             db.query(User).filter(User.id.in_([v.user_id for v in live])).all()} if live else {}
    return TicketPresenceResponse(
        ticket_id=t.id,
        viewers=[TicketViewerInfo(user_id=v.user_id, name=names.get(v.user_id),
                                  last_seen_at=v.last_seen_at, is_me=v.user_id == admin.id)
                 for v in live],
    )


def _validated_hold_code(code: str | None) -> str | None:
    """Normalize + validate a HoldReason taxonomy code. 422 on an unknown value."""
    if not code:
        return None
    v = str(code).strip().lower()
    if v not in HOLD_REASON_CODES:
        raise HTTPException(422, f"Unknown hold reason code '{code}'. Use one of: {', '.join(HOLD_REASON_CODES)}")
    return v


_MANUAL_ARCHIVE_CODES = [c for c in ARCHIVE_REASON_CODES if c != ArchiveReason.AUTO_RETENTION.value]


def _validated_archive_code(code: str | None) -> str | None:
    """Normalize + validate an ArchiveReason taxonomy code. 422 on an unknown value;
    'auto_retention' is reserved for the retention sweep and rejected on manual archives."""
    if not code:
        return None
    v = str(code).strip().lower()
    if v == ArchiveReason.AUTO_RETENTION.value:
        raise HTTPException(422, "'auto_retention' is reserved for the retention sweep.")
    if v not in _MANUAL_ARCHIVE_CODES:
        raise HTTPException(422, f"Unknown archive reason code '{code}'. Use one of: {', '.join(_MANUAL_ARCHIVE_CODES)}")
    return v


def _resume_target(t: SdTicket) -> str:
    """Where a lifted hold returns to: held_from_status, else in_progress when owned,
    else open — never an unassigned in_progress (mirrors _common.resume_held_ticket)."""
    fallback = TicketStatus.IN_PROGRESS.value if t.assigned_agent_id else TicketStatus.OPEN.value
    return t.held_from_status if t.held_from_status in OPEN_TICKET_STATUSES else fallback


@router.post("/{ticket_id}/hold", response_model=TicketResponse)
def hold_ticket(ticket_id: UUID, payload: TicketHold, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "place it on hold")
    if t.status == TicketStatus.ON_HOLD.value:
        raise HTTPException(409, "Ticket is already on hold — use hold-extend to update it")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Cannot hold a resolved/closed ticket")
    t.held_from_status = t.status
    t.held_at = sla_util.now_utc()
    t.hold_reason = payload.hold_reason
    t.hold_reason_code = _validated_hold_code(payload.hold_reason_code)
    t.hold_until = payload.hold_until
    _transition_status(db, t, TicketStatus.ON_HOLD.value, admin, note=payload.hold_reason)
    write_audit(db, entity_type="ticket", op="held", entity_id=t.id, actor_id=admin.id, request=request,
                details={"reason": payload.hold_reason, "code": t.hold_reason_code,
                         "until": payload.hold_until.isoformat() if payload.hold_until else None})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/resume", response_model=TicketResponse)
def resume_ticket(ticket_id: UUID, request: Request, payload: TicketResume | None = None,
                  db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "resume it")
    if t.status != TicketStatus.ON_HOLD.value:
        raise HTTPException(409, "Ticket is not on hold")
    target = _resume_target(t)
    note = (payload.reason.strip() if payload and payload.reason and payload.reason.strip()
            else "resumed from hold")
    # _transition_status's on-hold bookkeeping clears the hold context on the way out.
    _transition_status(db, t, target, admin, note=note)
    write_audit(db, entity_type="ticket", op="resumed", entity_id=t.id, actor_id=admin.id, request=request,
                details={"to": target, "reason": note})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/hold-extend", response_model=TicketResponse)
def extend_hold(ticket_id: UUID, payload: TicketHoldExtend, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Review/extend an ACTIVE hold without lifting it — push the release date, re-code the
    reason, leave a note. Stamps the hold-review governance fields so the stale-hold sweep
    knows the hold was deliberately re-confirmed."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "extend its hold")
    if t.status != TicketStatus.ON_HOLD.value:
        raise HTTPException(409, "Ticket is not on hold")
    nowt = sla_util.now_utc()
    changed: dict = {}
    if payload.hold_until is not None:
        changed["until"] = {"from": t.hold_until.isoformat() if t.hold_until else None,
                            "to": payload.hold_until.isoformat()}
        t.hold_until = payload.hold_until
    code = _validated_hold_code(payload.hold_reason_code)
    if code:
        changed["code"] = {"from": t.hold_reason_code, "to": code}
        t.hold_reason_code = code
    if payload.hold_reason is not None and payload.hold_reason.strip():
        changed["reason"] = {"from": t.hold_reason, "to": payload.hold_reason.strip()}
        t.hold_reason = payload.hold_reason.strip()
    t.last_hold_review_at = nowt
    t.hold_review_count = (t.hold_review_count or 0) + 1
    _log_activity(db, t, admin, "hold_extended",
                  {"note": payload.note, "review": t.hold_review_count, **changed})
    write_audit(db, entity_type="ticket", op="hold_extended", entity_id=t.id, actor_id=admin.id,
                request=request, details={"note": payload.note, **changed})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/reopen", response_model=TicketResponse)
def reopen_ticket(ticket_id: UUID, payload: TicketReopen, request: Request, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "reopen it")
    if t.status not in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Only resolved/closed tickets can be reopened")
    # A merged tombstone must never come back to life — reopening it would fork the
    # story into a zombie duplicate while merged_into_id still points at the master
    # (every stats surface excludes tombstones). Mirror the follow-up guard.
    if t.merged_into_id:
        master = db.query(SdTicket).filter(SdTicket.id == t.merged_into_id).first()
        raise HTTPException(409, f"This ticket was merged into {master.ticket_number if master else 'its master'} — reopen (or follow up on) the master instead.")
    # Reopen bookkeeping (reason/count/source/SLA re-arm) is owned by apply_reopen inside
    # the transition — single-writer rule, so this route stamps nothing itself.
    _transition_status(db, t, TicketStatus.IN_PROGRESS.value, admin, note=payload.reason,
                       reopen_source="agent", reopen_reason_code=payload.reason_code)
    write_audit(db, entity_type="ticket", op="reopened", entity_id=t.id, actor_id=admin.id, request=request,
                details={"reason": payload.reason, "reason_code": payload.reason_code, "source": "agent"})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


def _apply_restore(db: Session, t: SdTicket, admin: User, note: str | None = None) -> None:
    """Single-writer un-archive (route + bulk). Clears the tombstone AND the provenance
    stamps — the prior values survive on the 'restored' activity so the trail stays
    honest — releases any legal hold (only reachable here by a superuser; the callers
    guard), and pings the prior archiver + assignee so a disputed restore is visible."""
    prior = {"archived_at": t.archived_at, "archived_by_id": t.archived_by_id,
             "archive_reason_code": t.archive_reason_code, "legal_hold": bool(t.legal_hold)}
    prior_archiver = t.archived_by_id
    t.is_deleted = False
    t.archived_at = None
    t.archived_by_id = None
    t.archive_reason_code = None
    t.legal_hold = False
    detail = {"prior": prior}
    if note:
        detail["note"] = note
    _log_activity(db, t, admin, "restored", detail)
    for uid in {prior_archiver, t.assigned_agent_id} - {None, admin.id}:
        dispatch_safe(db, EVT_TICKET_RESTORED, uid, t,
                      title=f"Restored from archive: {t.subject}",
                      action_url=f"{_panel_base(db, uid)}/tickets/my")


@router.post("/{ticket_id}/restore", response_model=TicketResponse)
def restore_ticket(ticket_id: UUID, request: Request, payload: Optional[TicketRestore] = None,
                   db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    # archived tickets are is_deleted=True, so _get_ticket would 404 — fetch raw.
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    _require_ticket_scope(db, t, admin)
    _require_ticket_actor(db, t, admin, "restore it")
    if not t.is_deleted:
        raise HTTPException(409, "Ticket is not archived")
    # A held record can't be quietly restored (and re-archived, resetting the retention
    # clock) by a non-superuser — release the hold first.
    if t.legal_hold and not getattr(admin, "is_superuser", False):
        raise HTTPException(409, "This record is under legal hold — a superuser must release the hold first.")
    note = payload.note if payload else None
    _apply_restore(db, t, admin, note=note)
    write_audit(db, entity_type="ticket", op="restored", entity_id=t.id, actor_id=admin.id, request=request,
                details={"ticket_number": t.ticket_number, "note": note})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/legal-hold", response_model=TicketResponse)
def set_legal_hold(ticket_id: UUID, payload: TicketLegalHold, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Place / release a legal hold. A held record is exempt from the retention sweep and
    from purge eligibility. Placing a hold is an OWNER-TIER act (assignee / collaborator /
    owning-team lead / superuser — or claim-eligible on an unassigned ticket): it freezes
    retention, so a passing teammate shouldn't be able to do it to a colleague's ticket.
    Only a superuser may RELEASE a hold (403 otherwise). Raw fetch — works on live AND
    archived records."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    _require_ticket_scope(db, t, admin)
    want = bool(payload.hold)
    if not want and not getattr(admin, "is_superuser", False):
        raise HTTPException(403, "Only a superuser can release a legal hold.")
    if want:
        _require_ticket_actor(db, t, admin, "place a legal hold")
    if bool(t.legal_hold) == want:
        enrich_ticket(db, t)
        return TicketResponse.model_validate(t)
    t.legal_hold = want
    _log_activity(db, t, admin, "legal_hold_set" if want else "legal_hold_released",
                  {"note": payload.note} if payload.note else {})
    write_audit(db, entity_type="ticket", op="legal_hold_set" if want else "legal_hold_released",
                entity_id=t.id, actor_id=admin.id, request=request,
                details={"ticket_number": t.ticket_number, "hold": want, "note": payload.note})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


def _reap_blobs(att_lists) -> int:
    """Best-effort disk cleanup: delete the uploaded files referenced by attachment dicts
    ({file_url|url|file_path}, as minted by /uploads/file). Only paths inside the backend's
    /storage or /uploads mounts are touched — a hostile file_url can't escape the sandbox.
    Missing files are fine (already gone). Returns the count actually removed."""
    import os
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    safe = [(root / "storage").resolve(), (root / "uploads").resolve()]
    reaped = 0
    for atts in att_lists:
        for att in (atts or []):
            if not isinstance(att, dict):
                continue
            u = att.get("file_url") or att.get("url") or att.get("file_path")
            if not u:
                continue
            rel = str(u).split("?", 1)[0].lstrip("/\\")
            try:
                p = (root / rel).resolve()
            except Exception:  # noqa: BLE001 — malformed path, skip
                continue
            if not any(str(p).startswith(str(sr) + os.sep) for sr in safe):
                continue
            try:
                if p.is_file():
                    p.unlink()
                    reaped += 1
            except OSError:
                pass
    return reaped


@router.delete("/{ticket_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_ticket(ticket_id: UUID, request: Request,
                 reason: Optional[str] = Query(None, description="Why this record is being destroyed (audited)"),
                 db: Session = Depends(get_db),
                 su: User = Depends(get_current_superuser)):
    """PERMANENTLY destroy an archived record (retention end-of-life). Superuser only —
    the desk's single destructive act, never automatic. Guards: the record must be
    archived, past the retention window, not under legal hold, and not the master of a
    merge chain (tombstones point at it). The audit tombstone is written BEFORE the
    delete — it is the only trace that survives. ORM cascade removes comments +
    activities; presence rows are deleted; follow-up children and service requests are
    detached and live on. Attachment blobs (ticket + comments) are reaped from disk."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    if not t.is_deleted:
        raise HTTPException(409, "Only archived tickets can be purged — archive it first.")
    if t.legal_hold:
        raise HTTPException(409, "This record is under legal hold and cannot be purged.")
    nowt = sla_util.now_utc()
    eligible_at = (t.archived_at + timedelta(days=SUPPORT_ARCHIVE_RETENTION_DAYS)) if t.archived_at else None
    if not eligible_at or eligible_at > nowt:
        raise HTTPException(
            409, f"Not yet purge-eligible — the {SUPPORT_ARCHIVE_RETENTION_DAYS}-day retention window is still running.")
    merged_children = db.query(func.count(SdTicket.id)).filter(SdTicket.merged_into_id == t.id).scalar() or 0
    if merged_children:
        raise HTTPException(409, "Other tickets were merged into this record — it anchors their history and cannot be purged.")
    # Reap the uploaded blobs (ticket + every comment) — purge means GONE, disk included.
    comment_atts = [row[0] for row in db.query(SdTicketComment.attachments)
                    .filter(SdTicketComment.ticket_id == t.id).all()]
    reaped = _reap_blobs([t.attachments] + comment_atts)
    # Tombstone FIRST: after the delete, the audit ledger is the only trace.
    write_audit(db, entity_type="ticket", op="purged", entity_id=t.id, actor_id=su.id, request=request,
                details={"ticket_number": t.ticket_number, "subject": t.subject,
                         "status": t.status, "priority": t.priority,
                         "organization_id": str(t.organization_id) if t.organization_id else None,
                         "archived_at": t.archived_at.isoformat() if t.archived_at else None,
                         "archive_reason_code": t.archive_reason_code,
                         "attachments_reaped": reaped,
                         "reason": reason})
    from app.models.support_desk.workspace import SdTicketViewer
    db.query(SdTicketViewer).filter(SdTicketViewer.ticket_id == t.id).delete(synchronize_session=False)
    db.query(SdTicket).filter(SdTicket.follow_up_of_id == t.id).update(
        {"follow_up_of_id": None}, synchronize_session=False)
    try:
        from app.models.support_desk.catalog import SdServiceRequest
        db.query(SdServiceRequest).filter(SdServiceRequest.ticket_id == t.id).update(
            {"ticket_id": None}, synchronize_session=False)
    except Exception:
        pass
    db.delete(t)   # ORM cascade removes comments + activities (no DB-level CASCADE exists)
    db.commit()
    return None


# ─────────────────────── Resolve / Merge / Time (workbench) ───────────────────────
# Driven off the enum (single source of truth) — the old hardcoded set had drifted and
# was missing 'no_response' (the pending-customer auto-resolve code), so re-resolving
# such a ticket with the same code 422'd here while the self route accepted it.
_RESOLUTION_CODES = {c.value for c in ResolutionCode}
_ROOT_CAUSES = {c.value for c in RootCauseCategory}


def _apply_resolution(db: Session, t: SdTicket, admin: User, *, resolution_code: str,
                      resolution_category: str | None, resolution_summary: str | None,
                      time_spent_minutes: int | None, note: str | None,
                      attachments: list | None, close: bool) -> None:
    """Record a structured ITIL resolution on a ticket + stop the SLA clock. Shared by the
    single-ticket resolve route AND bulk resolve so both capture the same fields. Caller
    has already validated the code/category and the assignment/terminal guard."""
    t.resolution_code = resolution_code
    t.resolution_category = resolution_category
    t.resolution_summary = resolution_summary
    if time_spent_minutes:
        t.time_spent_minutes = (t.time_spent_minutes or 0) + time_spent_minutes
    # public resolution reply to the requester (+ optional proof attachments)
    if note or attachments:
        db.add(SdTicketComment(
            ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
            author_kind=CommentAuthorKind.STAFF.value,
            body=note or "Resolution evidence attached.",
            is_internal=False, attachments=attachments or []))
    target = TicketStatus.CLOSED.value if close else TicketStatus.RESOLVED.value
    _transition_status(db, t, target, admin, note=resolution_summary)
    _log_activity(db, t, admin, "resolved", {"code": resolution_code, "category": resolution_category})


@router.post("/{ticket_id}/resolve", response_model=TicketResponse)
def resolve_ticket(ticket_id: UUID, payload: TicketResolve, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Structured ITIL resolution: code + root cause + summary + time, stops the SLA
    clock (via _transition_status), optionally posts a public resolution reply."""
    if payload.resolution_code not in _RESOLUTION_CODES:
        raise HTTPException(422, f"Invalid resolution_code '{payload.resolution_code}'")
    if payload.resolution_category and payload.resolution_category not in _ROOT_CAUSES:
        raise HTTPException(422, f"Invalid resolution_category '{payload.resolution_category}'")
    require_resolution_summary(payload.resolution_summary)
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "resolve it")
    if t.status in TERMINAL_TICKET_STATUSES and not payload.close:
        raise HTTPException(409, "Ticket is already resolved/closed")
    # No owner ⇒ no resolution. You can't close the loop on work nobody was doing —
    # assign an owner first. (Closing an already-resolved ticket is exempt: it's just the
    # resolved→closed step, and it was assigned to be resolved in the first place.)
    if t.status not in TERMINAL_TICKET_STATUSES and not t.assigned_agent_id:
        raise HTTPException(409, "Assign an owner before resolving — nobody is working this ticket.")
    _apply_resolution(db, t, admin, resolution_code=payload.resolution_code,
                      resolution_category=payload.resolution_category,
                      resolution_summary=payload.resolution_summary,
                      time_spent_minutes=payload.time_spent_minutes,
                      note=payload.note, attachments=payload.attachments, close=payload.close)
    write_audit(db, entity_type="ticket", op="resolved", entity_id=t.id, actor_id=admin.id,
                request=request, details={"code": payload.resolution_code, "closed": payload.close})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/merge", response_model=TicketResponse)
def merge_ticket(ticket_id: UUID, payload: TicketMerge, request: Request,
                 db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Merge this (duplicate) ticket INTO the target master: link + close the duplicate
    and cross-note both. Idempotent — re-merging is a no-op."""
    if payload.target_id == ticket_id:
        raise HTTPException(422, "Cannot merge a ticket into itself")
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "merge it")
    master = _get_ticket(db, payload.target_id, admin)
    if t.merged_into_id == master.id:
        return TicketResponse.model_validate(enrich_ticket(db, t))  # already merged
    # Zendesk rule: you merge INTO the ticket where work CONTINUES. A resolved/closed
    # master can't carry the folded story — the requester would be stranded on a dead
    # record and a closed record is immutable (it continues via follow-up, not merges).
    if master.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, f"{master.ticket_number} is already {master.status.replace('_', ' ')} — "
                                 "merge into an active ticket, or reopen the master first.")
    # Cycle guard: walk the master's own merge chain — if it leads back to this ticket,
    # the merge would corrupt the lineage (A→B while B→…→A) and the chain walk would
    # loop forever conceptually. Merge into the chain's true master instead.
    _cur = master
    for _ in range(10):
        if _cur.id == t.id:
            raise HTTPException(422, "That target is already merged into this ticket's chain — merge into the chain's master instead.")
        if not _cur.merged_into_id:
            break
        _cur = db.query(SdTicket).filter(SdTicket.id == _cur.merged_into_id).first()
        if not _cur:
            break
    t.merged_into_id = master.id
    # Follow-up pins ride the merge: the work continues on the master, so any undone
    # reminders on the duplicate move with it (owner-private rows — owner unchanged).
    (db.query(SdTicketReminder)
       .filter(SdTicketReminder.ticket_id == t.id, SdTicketReminder.done == False)  # noqa: E712
       .update({SdTicketReminder.ticket_id: master.id}, synchronize_session=False))
    links = dict(t.links or {}); links["merged_into"] = str(master.id); t.links = links
    note = payload.comment or f"Merged into {master.ticket_number}."
    db.add(SdTicketComment(ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
                           author_kind=CommentAuthorKind.SYSTEM.value, body=note, is_internal=True))
    db.add(SdTicketComment(ticket_id=master.id, author_user_id=admin.id, author_name=_actor_name(admin),
                           author_kind=CommentAuthorKind.SYSTEM.value,
                           body=f"{t.ticket_number} was merged into this ticket.", is_internal=True))
    _log_activity(db, t, admin, "merged", {"into": master.ticket_number})
    if t.status not in TERMINAL_TICKET_STATUSES:
        _transition_status(db, t, TicketStatus.CLOSED.value, admin, note=f"merged into {master.ticket_number}")
    write_audit(db, entity_type="ticket", op="merged", entity_id=t.id, actor_id=admin.id,
                request=request, details={"into": str(master.id), "master_number": master.ticket_number})
    if master.assigned_agent_id and master.assigned_agent_id != admin.id:
        dispatch_safe(db, EVT_TICKET_MERGED, master.assigned_agent_id, master,
                      title=f"{t.ticket_number} merged into {master.ticket_number}",
                      action_url="/admin/support-desk/tickets")
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────── Closed desk: follow-up / KB promote / merge chain ───────────────────
@router.post("/{ticket_id}/follow-up", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_follow_up(ticket_id: UUID, payload: TicketFollowUpCreate, request: Request,
                     db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Zendesk-pattern follow-up: a CLOSED record is immutable for requesters — the
    sanctioned way to continue its story is a fresh, linked ticket. Copies the original's
    requester/org/category context, links back via follow_up_of_id, arms a fresh SLA and
    rides normal routing. Terminal tickets only; a merged tombstone points at its master."""
    t = _get_ticket(db, ticket_id, admin)
    if t.status not in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Follow-ups are for finished tickets — this one is still being worked.")
    if t.merged_into_id:
        master = db.query(SdTicket.ticket_number).filter(SdTicket.id == t.merged_into_id).scalar()
        raise HTTPException(409, f"This ticket was merged into {master or 'its master'} — follow up the master record instead.")
    priority = payload.priority or t.priority
    ticket_type = payload.ticket_type or t.ticket_type
    if priority not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{priority}'")
    if ticket_type not in _TYPES:
        raise HTTPException(422, f"Invalid ticket_type '{ticket_type}'")

    number = generate_ticket_number(db)
    pkg = resolve_sla_package(db, t.sla_package_id, t.organization_id)
    rd, rsd = sla_util.compute_deadlines(pkg, priority)
    child = SdTicket(
        ticket_number=number,
        subject=payload.subject or f"Follow-up: {t.subject}",
        description=payload.description,
        category_id=payload.category_id or t.category_id,
        subcategory_id=t.subcategory_id if not payload.category_id else None,
        ticket_type=ticket_type,
        priority=priority,
        source=t.source,
        status=TicketStatus.OPEN.value,
        organization_id=t.organization_id,
        customer_id=t.customer_id,
        contact_name=t.contact_name,
        contact_email=t.contact_email,
        contact_phone=t.contact_phone,
        department=t.department,
        location=t.location,
        # The requester keeps the story: their portal/self-service view follows the chain.
        is_internal=t.is_internal,
        raised_by_user_id=t.raised_by_user_id,
        team_id=t.team_id,
        assigned_agent_id=admin.id if payload.assign_me else None,
        follow_up_of_id=t.id,
        links={"follow_up_of": str(t.id)},
        sla_package_id=pkg.id if pkg else t.sla_package_id,
        response_due_at=rd,
        resolution_due_at=rsd,
        tags=payload.tags or [],
        created_by_id=admin.id,
    )
    db.add(child)
    db.flush()
    _log_activity(db, child, admin, "created",
                  {"ticket_number": number, "priority": priority, "follow_up_of": t.ticket_number})
    _log_activity(db, t, admin, "follow_up_created", {"child": number, "child_id": str(child.id)})
    db.add(SdTicketComment(ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
                           author_kind=CommentAuthorKind.SYSTEM.value,
                           body=f"Follow-up {number} was opened to continue this record.", is_internal=True))
    db.add(SdTicketComment(ticket_id=child.id, author_user_id=admin.id, author_name=_actor_name(admin),
                           author_kind=CommentAuthorKind.SYSTEM.value,
                           body=f"Opened as a follow-up of {t.ticket_number}.", is_internal=True))
    evaluate_rules(db, child)
    route_and_assign(db, child)
    apply_default_queue(db, child)
    if child.assigned_agent_id and child.assigned_agent_id != admin.id:
        dispatch_safe(db, EVT_TICKET_ASSIGNED, child.assigned_agent_id, child,
                      title=f"Assigned: {child.subject}",
                      action_url=f"{_panel_base(db, child.assigned_agent_id)}/tickets/my?ticket={child.id}")
    write_audit(db, entity_type="ticket", op="follow_up_created", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"child": number, "child_id": str(child.id)})
    db.commit(); db.refresh(child)
    return TicketResponse.model_validate(enrich_ticket(db, child))


@router.post("/{ticket_id}/promote-article", status_code=status.HTTP_201_CREATED)
def promote_to_kb(ticket_id: UUID, payload: TicketKbPromote, request: Request,
                  db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """KCS (ServiceNow knowledge-candidate pattern): promote a sealed ticket's resolution
    into a DRAFT knowledge article. Status is server-forced to draft — publishing stays an
    editorial act. Idempotent: if this ticket already seeded an article, return it (200
    semantics via the same shape) instead of minting a duplicate."""
    from app.models.support_desk.catalog import SdKnowledgeArticle
    from app.models.support_desk.constants import ArticleStatus, ArticleVisibility
    from app.schemas.support_desk.catalog import ArticleResponse

    t = _get_ticket(db, ticket_id, admin)
    if t.status not in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Promote finished tickets only — resolve it first so the fix is on record.")
    existing_id = (t.links or {}).get("kb_article_id")
    if existing_id:
        a = db.query(SdKnowledgeArticle).filter(
            SdKnowledgeArticle.id == existing_id,
            SdKnowledgeArticle.is_deleted == False).first()  # noqa: E712
        if a:
            return ArticleResponse.model_validate(a)
    body = (payload.body or "").strip()
    if not body:
        if not t.resolution_summary or len(t.resolution_summary.strip()) < 3:
            raise HTTPException(422, "Nothing to promote — this record has no resolution summary.")
        cat_name = None
        if t.category_id:
            from app.models.support_desk.core import SdCategory
            cat_name = db.query(SdCategory.name).filter(SdCategory.id == t.category_id).scalar()
        parts = [
            "## Problem", t.subject or "", "", (t.description or "").strip(), "",
            "## Environment",
            f"- Type: {t.ticket_type or 'n/a'}" + (f" / {cat_name}" if cat_name else ""),
            f"- Priority: {t.priority or 'n/a'}", "",
            f"## Resolution ({t.resolution_code or 'solved'})",
            t.resolution_summary.strip(),
        ]
        if t.resolution_category:
            parts += ["", "## Root cause", t.resolution_category]
        body = "\n".join(parts)
    visibility = payload.visibility or ArticleVisibility.INTERNAL.value
    if visibility not in {v.value for v in ArticleVisibility}:
        raise HTTPException(422, f"Invalid visibility '{visibility}'")
    a = SdKnowledgeArticle(
        title=(payload.title or f"{t.subject} — {t.resolution_code or 'resolution'}")[:300],
        category_id=payload.kb_category_id,
        short_description=f"Harvested from {t.ticket_number}.",
        content=body,
        tags=list(t.tags or []),
        visibility=visibility,
        status=ArticleStatus.DRAFT.value,   # server-forced: promotion never publishes
        author_id=admin.id,
    )
    db.add(a)
    db.flush()
    links = dict(t.links or {}); links["kb_article_id"] = str(a.id); t.links = links
    _log_activity(db, t, admin, "kb_promoted", {"article_id": str(a.id), "title": a.title})
    write_audit(db, entity_type="ticket", op="kb_promoted", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"article_id": str(a.id), "title": a.title})
    db.commit(); db.refresh(a)
    return ArticleResponse.model_validate(a)


@router.get("/{ticket_id}/merge-chain", response_model=TicketMergeChain)
def merge_chain(ticket_id: UUID, db: Session = Depends(get_db),
                admin: User = Depends(get_support_agent)):
    """The merge lineage of a record: masters walked UP merged_into_id (loop-guarded,
    capped) + the duplicates folded INTO this ticket. Read-only, powers the closure
    certificate's chain graph."""
    t = _get_ticket(db, ticket_id, admin)

    def _node(x: SdTicket) -> MergeChainNode:
        return MergeChainNode(id=x.id, ticket_number=x.ticket_number, subject=x.subject,
                              status=x.status, closed_at=x.closed_at,
                              merged_into_id=x.merged_into_id)

    masters, seen, cur = [], {t.id}, t
    for _ in range(10):
        if not cur.merged_into_id or cur.merged_into_id in seen:
            break
        cur = db.query(SdTicket).filter(SdTicket.id == cur.merged_into_id,
                                        SdTicket.is_deleted == False).first()  # noqa: E712
        if not cur:
            break
        seen.add(cur.id)
        masters.append(_node(cur))
    duplicates = [_node(x) for x in
                  db.query(SdTicket).filter(SdTicket.merged_into_id == t.id,
                                            SdTicket.is_deleted == False)  # noqa: E712
                  .order_by(SdTicket.closed_at.desc().nullslast()).limit(50).all()]
    return TicketMergeChain(masters=masters, duplicates=duplicates)


@router.post("/{ticket_id}/time", response_model=TicketResponse)
def log_time(ticket_id: UUID, payload: TicketTimeLog, request: Request,
             db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    t = _get_ticket(db, ticket_id, admin)
    # Owner-tier like every other workbench mutation — a teammate must not pad (or
    # pollute) the effort record of a ticket they don't work.
    _require_ticket_actor(db, t, admin, "log time on it")
    t.time_spent_minutes = (t.time_spent_minutes or 0) + payload.minutes
    _log_activity(db, t, admin, "time_logged", {"minutes": payload.minutes, "note": payload.note})
    db.commit(); db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.post("/{ticket_id}/viewed", status_code=status.HTTP_204_NO_CONTENT)
def mark_viewed(ticket_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Lightweight last-viewed stamp (no audit/activity — avoids timeline spam).
    Out-of-scope tickets are silently skipped (fire-and-forget endpoint — the drawer
    already degrades to requester mode on the real fetch)."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if t:
        try:
            _require_ticket_scope(db, t, admin)
        except HTTPException:
            return None
        t.last_viewed_at = sla_util.now_utc()
        db.commit()
    return None


# ─────────────────────────────── Bulk ───────────────────────────────
_BULK_ACTIONS = {"assign", "escalate", "resolve", "close", "set_status", "set_priority", "add_tag",
                 "vendor_chase", "vendor_bring_back", "set_vendor_due", "hold", "resume", "ack",
                 "escalation_ack", "de_escalate", "restore", "legal_hold"}
# Bulk actions that COMMAND a ticket's workflow — owner-tier per ticket (assignee /
# collaborator / team lead / superuser; unassigned rows are team triage). Mirrors the
# single-ticket _require_ticket_actor gates. add_tag (light edit) and legal_hold
# (place — any in-scope agent; release is superuser-guarded above) stay team-open.
# legal_hold is owner-tier like its single-ticket route (placing a hold freezes retention —
# not a passing-teammate act); release stays superuser-only (guarded above). add_tag remains
# team-open (any in-scope agent may tag).
_BULK_OWNER_TIER = _BULK_ACTIONS - {"add_tag"}


@router.post("/bulk", response_model=TicketBulkResponse)
def bulk_action(
    payload: TicketBulkAction,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Apply one action across many tickets. Every ticket is guarded against the status
    workflow (assignment-before-work, no direct close, reopen-before-edit) — ineligible
    rows are SKIPPED with a reason, not silently mutated and not fatal to the batch.
    Idempotent. One transaction, committed once."""
    action = payload.action
    if action not in _BULK_ACTIONS:
        raise HTTPException(422, f"Invalid bulk action '{action}'")
    if action == "set_status":
        if payload.status not in _STATUSES:
            raise HTTPException(422, f"Invalid status '{payload.status}'")
        # generic status change may only target work states — resolve/close/escalate
        # have dedicated actions that capture a resolution / reason.
        if payload.status not in _WORK_STATUSES:
            raise HTTPException(422, "Use the resolve or escalate action for that status, not set_status.")
    if action == "set_priority" and payload.priority not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{payload.priority}'")
    if action == "assign" and not payload.assigned_agent_id:
        raise HTTPException(422, "assigned_agent_id is required for assign")
    if action == "add_tag" and not (payload.tag or "").strip():
        raise HTTPException(422, "tag is required for add_tag")
    if action in ("resolve", "close"):
        if payload.resolution_code and payload.resolution_code not in _RESOLUTION_CODES:
            raise HTTPException(422, f"Invalid resolution_code '{payload.resolution_code}'")
        if payload.resolution_category and payload.resolution_category not in _ROOT_CAUSES:
            raise HTTPException(422, f"Invalid resolution_category '{payload.resolution_category}'")
        require_resolution_summary(payload.resolution_summary)
    if action == "escalate":
        if not (payload.reason or "").strip():
            raise HTTPException(422, "A reason is required to escalate.")
        if payload.reason_code and payload.reason_code not in ESCALATION_REASON_CODES:
            raise HTTPException(422, f"Invalid reason_code '{payload.reason_code}'")
        if payload.escalation_type and payload.escalation_type not in ESCALATION_TYPES:
            raise HTTPException(422, f"Invalid escalation_type '{payload.escalation_type}'")
        if payload.team_id:
            # Mirror the single-ticket escalate: the functional target must be a real,
            # live team — bulk previously stamped any UUID straight onto team_id.
            from app.models.support_desk.workspace import SdTeam
            if not db.query(SdTeam.id).filter(SdTeam.id == payload.team_id,
                                              SdTeam.is_deleted == False).first():  # noqa: E712
                raise HTTPException(404, "Target team not found")
    if action == "de_escalate" and not (payload.reason or "").strip():
        raise HTTPException(422, "A reason is required to de-escalate.")
    if action == "set_vendor_due" and payload.vendor_due_at is None:
        raise HTTPException(422, "vendor_due_at is required for set_vendor_due.")
    if action == "hold":
        _validated_hold_code(payload.hold_reason_code)  # 422 early on a bad taxonomy code
    if action == "legal_hold":
        if payload.hold is None:
            raise HTTPException(422, "hold (true/false) is required for legal_hold")
        if payload.hold is False and not getattr(admin, "is_superuser", False):
            raise HTTPException(403, "Only a superuser can release legal holds.")

    # Team-seal for non-superuser agents: bulk previously trusted raw ids, so an agent could
    # mutate ANOTHER team's tickets by id. Pre-filter the batch through the same
    # _command_center_filter the list uses (single source of truth) — one extra query total.
    _cond, _bulk_ctx = _agent_scope(db, admin)
    allowed_ids: set | None = None
    if _cond is not None:
        allowed_ids = {row[0] for row in
                       db.query(SdTicket.id).filter(SdTicket.id.in_(payload.ids), _cond).all()}

    # Non-superuser assign targets route within reach (per owning team; cached per team_id).
    _pool_cache: dict = {}

    def _assign_pool(team_id):
        if team_id not in _pool_cache:
            from app.routers.support_desk.tickets_self import _team_members_of
            base_pool = _team_members_of(db, team_id) if team_id else set(_bulk_ctx["member_ids"])
            _pool_cache[team_id] = (base_pool | _bulk_ctx["led_member_ids"]
                                    | _bulk_ctx["reports"] | {admin.id})
        return _pool_cache[team_id]

    results: list[TicketBulkResult] = []
    updated = 0
    skipped = 0
    for tid in payload.ids:
        if allowed_ids is not None and tid not in allowed_ids:
            results.append(TicketBulkResult(id=tid, ok=False, error="Not in your team's scope"))
            continue
        # restore / legal_hold operate ON archived rows — every other action must keep
        # 404-ing tombstones (they're read-only outside the Deep Storage lifecycle).
        if action in ("restore", "legal_hold"):
            t = db.query(SdTicket).filter(SdTicket.id == tid).first()
        else:
            t = db.query(SdTicket).filter(SdTicket.id == tid, SdTicket.is_deleted == False).first()  # noqa: E712
        if not t:
            results.append(TicketBulkResult(id=tid, ok=False, error="Ticket not found"))
            continue
        # Per-ticket owner-tier gate (assignee / collaborator / lead / triage) — a teammate's
        # assigned ticket is skipped with the reason, mirroring the single-ticket 403s.
        if action in _BULK_OWNER_TIER:
            actor_err = _ticket_actor_error(t, admin, _bulk_ctx, db)
            if actor_err:
                skipped += 1
                results.append(TicketBulkResult(id=tid, ok=True, skipped=True,
                                                error=actor_err, ticket_number=t.ticket_number))
                continue
        # Non-superuser assign/escalate-with-owner: the new owner must be within reach.
        if (_bulk_ctx is not None and payload.assigned_agent_id
                and action in ("assign", "escalate")
                and payload.assigned_agent_id not in _assign_pool(t.team_id)):
            skipped += 1
            results.append(TicketBulkResult(id=tid, ok=True, skipped=True,
                                            error="That assignee isn't on this ticket's team.",
                                            ticket_number=t.ticket_number))
            continue
        try:
            terminal = t.status in TERMINAL_TICKET_STATUSES
            skip_reason: str | None = None
            changed = False

            if action == "assign":
                if terminal:
                    skip_reason = "Resolved/closed — reopen before reassigning."
                elif t.assigned_agent_id == payload.assigned_agent_id:
                    skip_reason = "Already assigned to that agent."
                else:
                    t.assigned_agent_id = payload.assigned_agent_id
                    _log_activity(db, t, admin, "assigned", {"assigned_agent_id": str(payload.assigned_agent_id)})
                    dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                                  title=f"Assigned: {t.subject}",
                                  action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/my?ticket={t.id}")
                    changed = True

            elif action == "escalate":
                if terminal:
                    skip_reason = "Cannot escalate a resolved/closed ticket."
                elif not t.assigned_agent_id and not payload.assigned_agent_id:
                    skip_reason = "Assign an owner before escalating — nobody is working this ticket."
                else:
                    if payload.assigned_agent_id:
                        t.assigned_agent_id = payload.assigned_agent_id
                    if payload.support_team:
                        t.support_team = payload.support_team
                    if payload.team_id:
                        t.team_id = payload.team_id
                    changed = _do_escalate(
                        db, t, admin, reason=payload.reason,
                        reason_code=payload.reason_code,
                        escalation_type=payload.escalation_type or ("functional" if payload.team_id else None),
                        to_team_id=payload.team_id,
                        response_minutes=payload.response_minutes)

            elif action == "escalation_ack":
                if terminal:
                    skip_reason = "Resolved/closed — nothing to acknowledge."
                elif not t.is_escalated:
                    skip_reason = "Not escalated."
                elif t.escalation_acknowledged_at:
                    skip_reason = "Escalation already acknowledged."
                else:
                    t.escalation_acknowledged_at = sla_util.now_utc()
                    t.escalation_acknowledged_by_id = admin.id
                    _log_activity(db, t, admin, "escalation_acknowledged",
                                  {"level": t.escalation_level, "bulk": True})
                    changed = True

            elif action == "de_escalate":
                if not t.is_escalated and (t.escalation_level or 0) <= 0:
                    skip_reason = "Not escalated."
                else:
                    _do_de_escalate(db, t, admin, payload.reason.strip())
                    changed = True

            elif action in ("resolve", "close"):
                if terminal:
                    skip_reason = "Already resolved/closed."
                elif not t.assigned_agent_id:
                    skip_reason = "Assign an owner before resolving — nobody is working this ticket."
                else:
                    _apply_resolution(
                        db, t, admin,
                        resolution_code=payload.resolution_code or "solved",
                        resolution_category=payload.resolution_category,
                        resolution_summary=payload.resolution_summary,
                        time_spent_minutes=payload.time_spent_minutes,
                        note=payload.note, attachments=None, close=(action == "close"))
                    changed = True

            elif action == "set_status":
                skip_reason = _set_status_guard(t, payload.status)
                if not skip_reason:
                    changed = _transition_status(db, t, payload.status, admin, payload.note)

            elif action == "set_priority":
                if terminal:
                    skip_reason = "Resolved/closed — priority no longer applies."
                elif t.priority == payload.priority:
                    skip_reason = f"Already {payload.priority}."
                else:
                    old = t.priority
                    t.priority = payload.priority
                    if t.first_responded_at is None:
                        pkg = resolve_sla_package(db, t.sla_package_id, t.organization_id)
                        rd, rsd = sla_util.compute_deadlines(pkg, t.priority, start=t.created_at)
                        t.sla_package_id = pkg.id if pkg else t.sla_package_id
                        t.response_due_at, t.resolution_due_at = rd, rsd
                    _log_activity(db, t, admin, "updated", {"changes": {"priority": {"from": old, "to": payload.priority}}})
                    changed = True

            elif action == "add_tag":
                tagv = payload.tag.strip()
                cur = list(t.tags or [])
                if tagv in cur:
                    skip_reason = "Tag already present."
                else:
                    t.tags = cur + [tagv]   # reassign (no in-place mutation → no flag_modified needed)
                    _log_activity(db, t, admin, "updated", {"changes": {"tag_added": tagv}})
                    changed = True

            elif action == "vendor_chase":
                if t.status != TicketStatus.PENDING_VENDOR.value:
                    skip_reason = "Not waiting on a vendor — nothing to chase."
                else:
                    t.vendor_reminder_count = (t.vendor_reminder_count or 0) + 1
                    t.last_vendor_reminder_at = sla_util.now_utc()
                    _log_activity(db, t, admin, "vendor_chased", {"count": t.vendor_reminder_count, "message": payload.message})
                    changed = True

            elif action == "vendor_bring_back":
                if t.status != TicketStatus.PENDING_VENDOR.value:
                    skip_reason = "Not waiting on a vendor — nothing to bring back."
                else:
                    t.vendor_reply_at = sla_util.now_utc()
                    changed = _transition_status(db, t, TicketStatus.IN_PROGRESS.value, admin, note="vendor replied (bulk)")

            elif action == "set_vendor_due":
                if t.status != TicketStatus.PENDING_VENDOR.value:
                    skip_reason = "Not waiting on a vendor — no ETA to set."
                else:
                    t.vendor_due_at = payload.vendor_due_at
                    t.vendor_overdue_flagged = False
                    _log_activity(db, t, admin, "updated", {"changes": {"vendor_due_at": str(payload.vendor_due_at)}})
                    changed = True

            elif action == "hold":
                if terminal:
                    skip_reason = "Cannot hold a resolved/closed ticket."
                elif t.status == TicketStatus.ON_HOLD.value:
                    skip_reason = "Already on hold."
                else:
                    t.held_from_status = t.status
                    t.held_at = sla_util.now_utc()
                    t.hold_reason = payload.hold_reason
                    t.hold_reason_code = _validated_hold_code(payload.hold_reason_code)
                    t.hold_until = payload.hold_until
                    changed = _transition_status(db, t, TicketStatus.ON_HOLD.value, admin,
                                                 note=payload.hold_reason or payload.note)

            elif action == "resume":
                if t.status != TicketStatus.ON_HOLD.value:
                    skip_reason = "Not on hold — nothing to resume."
                else:
                    changed = _transition_status(db, t, _resume_target(t), admin,
                                                 note=payload.note or "resumed from hold (bulk)")

            elif action == "ack":
                if terminal:
                    skip_reason = "Resolved/closed — nothing to acknowledge."
                elif t.acknowledged_at:
                    skip_reason = "Already acknowledged."
                else:
                    t.acknowledged_at = sla_util.now_utc()
                    t.acknowledged_by_id = admin.id
                    _log_activity(db, t, admin, "acknowledged", {"bulk": True})
                    changed = True

            elif action == "restore":
                if not t.is_deleted:
                    skip_reason = "Not archived."
                elif t.legal_hold and not getattr(admin, "is_superuser", False):
                    skip_reason = "Under legal hold — a superuser must release it first."
                else:
                    _apply_restore(db, t, admin, note=payload.note)
                    changed = True

            elif action == "legal_hold":
                want = bool(payload.hold)
                if bool(t.legal_hold) == want:
                    skip_reason = "Already under legal hold." if want else "No legal hold to release."
                else:
                    t.legal_hold = want
                    _log_activity(db, t, admin, "legal_hold_set" if want else "legal_hold_released",
                                  {"bulk": True, **({"note": payload.note} if payload.note else {})})
                    changed = True

            if changed:
                updated += 1
                results.append(TicketBulkResult(id=tid, ok=True, ticket_number=t.ticket_number))
            else:
                skipped += 1
                results.append(TicketBulkResult(id=tid, ok=True, skipped=True,
                                                 error=skip_reason or "No change.",
                                                 ticket_number=t.ticket_number))
        except Exception as e:  # noqa: BLE001 — record + continue, never poison the batch
            results.append(TicketBulkResult(id=tid, ok=False, error=str(e)[:160],
                                            ticket_number=getattr(t, "ticket_number", None)))

    # audit_logs.entity_id is NOT NULL — anchor the summary on the first id in the batch.
    write_audit(db, entity_type="ticket", op="bulk", entity_id=payload.ids[0],
                actor_id=admin.id, request=request,
                details={"action": action, "count": len(payload.ids), "updated": updated,
                         "skipped": skipped, "ids": [str(i) for i in payload.ids]})
    db.commit()
    return TicketBulkResponse(updated=updated, skipped=skipped, results=results)


# ─────────────────────────────── CSAT ───────────────────────────────
@router.post("/{ticket_id}/csat", response_model=TicketResponse)
def set_csat(
    ticket_id: UUID,
    payload: TicketCsat,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "record a rating")
    # CSAT is the customer's verdict ON A FIX — there is nothing to rate while the
    # ticket is still being worked (ServiceNow/Zendesk send the survey at resolve).
    if t.status not in (TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value):
        raise HTTPException(409, "CSAT records the customer's verdict on a fix — resolve the ticket first.")
    # Verdict-of-record guard (Closed desk): once a record is SEALED with a customer
    # rating, an agent must not rewrite it — that CSAT is the archived verdict on the
    # fix. Superusers may correct genuine mistakes; the edit stays on the timeline.
    if (t.status == TicketStatus.CLOSED.value and t.csat_score is not None
            and not getattr(admin, "is_superuser", False)):
        raise HTTPException(409, "The customer's rating on a closed ticket is the verdict of record — it can't be overwritten.")
    t.csat_score = payload.csat_score
    t.csat_comment = payload.csat_comment
    _log_activity(db, t, admin, "csat", {"score": payload.csat_score})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Comments ───────────────────────────────
@router.get("/{ticket_id}/comments", response_model=list[CommentResponse])
def list_comments(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    _get_ticket(db, ticket_id, admin)
    return (db.query(SdTicketComment).filter(SdTicketComment.ticket_id == ticket_id)
            .order_by(SdTicketComment.created_at).all())


@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def add_comment(
    ticket_id: UUID,
    payload: CommentCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id, admin)
    c = SdTicketComment(
        ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
        author_kind=CommentAuthorKind.STAFF.value, body=payload.body,
        is_internal=payload.is_internal, attachments=payload.attachments or [],
    )
    db.add(c)
    # First public staff reply stops the response clock.
    if not payload.is_internal and t.first_responded_at is None:
        t.first_responded_at = sla_util.now_utc()
        sla_util.recompute_breach_flags(t)
    _log_activity(db, t, admin, "internal_note" if payload.is_internal else "replied",
                  {"preview": payload.body[:80]})
    if not payload.is_internal and t.raised_by_user_id:
        dispatch_safe(db, EVT_TICKET_REPLIED, t.raised_by_user_id, t,
                      title=f"Reply on {t.ticket_number}", action_url="/user/support/tickets")
    write_audit(db, entity_type="ticket", op="commented", entity_id=t.id,
                actor_id=admin.id, request=request, details={"internal": payload.is_internal})
    db.commit()
    db.refresh(c)
    return c


@router.post("/{ticket_id}/comments/{comment_id}/redact", response_model=CommentResponse)
def redact_comment(ticket_id: UUID, comment_id: UUID, payload: CommentRedact, request: Request,
                   db: Session = Depends(get_db), su: User = Depends(get_current_superuser)):
    """Destructively scrub a comment's content (Zendesk redaction parity). SUPERUSER ONLY —
    redaction removes evidence, so it is the admin's judgement call and always audited.
    Works on live AND archived records (sensitive data hides in deep storage too). The
    body is replaced with a tombstone, attachment blobs are deleted from disk, and the
    who/when/why survive on the comment + timeline + audit ledger. Irreversible."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    c = db.query(SdTicketComment).filter(SdTicketComment.id == comment_id,
                                         SdTicketComment.ticket_id == t.id).first()
    if not c:
        raise HTTPException(404, "Comment not found")
    if c.is_redacted:
        raise HTTPException(409, "This comment is already redacted.")
    reaped = _reap_blobs([c.attachments])   # blobs go BEFORE the URL list is cleared
    orig_chars = len(c.body or "")
    c.body = "■ This message was redacted by an administrator."
    c.attachments = []
    c.is_redacted = True
    c.redacted_by_id = su.id
    c.redacted_at = sla_util.now_utc()
    c.redacted_reason = payload.reason.strip()
    _log_activity(db, t, su, "comment_redacted",
                  {"comment_id": str(c.id), "author": c.author_name,
                   "reason": c.redacted_reason, "chars_removed": orig_chars,
                   "attachments_reaped": reaped})
    write_audit(db, entity_type="ticket", op="comment_redacted", entity_id=t.id,
                actor_id=su.id, request=request,
                details={"ticket_number": t.ticket_number, "comment_id": str(c.id),
                         "author": c.author_name, "reason": c.redacted_reason,
                         "chars_removed": orig_chars, "attachments_reaped": reaped})
    db.commit()
    db.refresh(c)
    return c


@router.post("/{ticket_id}/change-requester", response_model=TicketResponse)
def change_requester(ticket_id: UUID, payload: TicketChangeRequester, request: Request,
                     db: Session = Depends(get_db), su: User = Depends(get_current_superuser)):
    """Re-home a ticket to a different requester (ServiceNow 'change caller' / Zendesk
    'change requester'). SUPERUSER ONLY — requester identity drives visibility (My
    Tickets, notifications, the reopen right), so this is an admin correction, always
    audited. Archived records are exempt (restore first); both parties are notified."""
    if not payload.raised_by_user_id:
        raise HTTPException(422, "raised_by_user_id is required.")
    t = _get_ticket(db, ticket_id)   # 404s archived tombstones
    if str(t.raised_by_user_id or "") == str(payload.raised_by_user_id):
        raise HTTPException(409, "That user is already the requester.")
    target = db.query(User).filter(User.id == payload.raised_by_user_id,
                                   User.is_active == True).first()  # noqa: E712
    if not target:
        raise HTTPException(404, "User not found or inactive")
    prev_id = t.raised_by_user_id
    prev_name = (db.query(User.full_name).filter(User.id == prev_id).scalar()
                 if prev_id else (t.contact_name or None))
    t.raised_by_user_id = target.id
    if t.is_internal:
        # Internal tickets mirror the requester on the contact card — keep them in step.
        t.contact_name = target.full_name or t.contact_name
        t.contact_email = target.email or t.contact_email
    detail = {"from_id": str(prev_id) if prev_id else None, "from_name": prev_name,
              "to_id": str(target.id), "to_name": target.full_name,
              **({"reason": payload.reason} if payload.reason else {})}
    _log_activity(db, t, su, "requester_changed", detail)
    write_audit(db, entity_type="ticket", op="requester_changed", entity_id=t.id,
                actor_id=su.id, request=request,
                details={"ticket_number": t.ticket_number, **detail})
    dispatch_safe(db, EVT_TICKET_STATUS, target.id, t,
                  title=f"{t.ticket_number} is now your ticket",
                  action_url="/user/support/tickets")
    if prev_id and prev_id != target.id:
        dispatch_safe(db, EVT_TICKET_STATUS, prev_id, t,
                      title=f"{t.ticket_number} was re-homed to {target.full_name or 'another employee'}",
                      action_url="/user/support/tickets")
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.get("/{ticket_id}/activities", response_model=list[ActivityResponse])
def list_activities(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    # Raw fetch (not _get_ticket): the timeline of an ARCHIVED record must stay readable —
    # the Deep Storage certificate shows who archived it and every prior life event. Still
    # team-sealed: an out-of-scope timeline is as private as the ticket itself.
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    _require_ticket_scope(db, t, admin)
    return (db.query(SdTicketActivity).filter(SdTicketActivity.ticket_id == ticket_id)
            .order_by(SdTicketActivity.created_at).all())


# ─────────────────────────────── Delete + portal ───────────────────────────────
@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(
    ticket_id: UUID,
    request: Request,
    reason: Optional[str] = Query(None, description="Why this ticket is being archived (audited free text)"),
    reason_code: Optional[str] = Query(None, description="Coded archive taxonomy — one of the manual ArchiveReason codes"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Soft-delete (archive) — sets is_deleted=True and stamps the archive provenance
    (archived_at / archived_by / archive_reason_code). The ticket is hidden from live
    lists but fully restorable via POST /{id}/restore until a superuser purges it after
    the retention window. Never hard-destroys the row here."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "archive it")
    code = _validated_archive_code(reason_code)
    t.is_deleted = True
    t.archived_at = sla_util.now_utc()
    t.archived_by_id = admin.id
    t.archive_reason_code = code
    _log_activity(db, t, admin, "archived",
                  {k: v for k, v in {"reason": reason, "reason_code": code}.items() if v})
    write_audit(db, entity_type="ticket", op="deleted", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"ticket_number": t.ticket_number, "reason": reason, "reason_code": code})
    # The owner learns their ticket left circulation (never the requester — internal op).
    if t.assigned_agent_id and t.assigned_agent_id != admin.id:
        dispatch_safe(db, EVT_TICKET_ARCHIVED, t.assigned_agent_id, t,
                      title=f"Archived: {t.subject}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/tickets/archived")
    db.commit()
    return None


@router.post("/{ticket_id}/portal/rotate")
def rotate_portal_token(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Mint/refresh the public-portal token + security window for client access."""
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "rotate its portal link")
    t.public_token = secrets.token_urlsafe(32)
    t.public_token_expires_at = sla_util.now_utc() + timedelta(days=PORTAL_TOKEN_TTL_DAYS)
    db.commit()
    return {"public_token": t.public_token, "public_token_expires_at": t.public_token_expires_at}


# ─────────────────────────────── notify helper ───────────────────────────────
def dispatch_safe(db: Session, event: str, recipient_user_id, ticket: SdTicket, *,
                  title: str, action_url: str):
    """Thin wrapper around HR notify.dispatch — Support Desk's first production caller."""
    try:
        from app.utils.hr.notify import dispatch
        from app.utils.support_desk import wires
        wires.post_webhook(db, event, ticket, title)   # external uplink mirrors the event
        if not wires.allows(db, event):                # panel wire cut → no agent ping
            return
        # Deep-link straight to the ticket so the bell click opens its drawer.
        deep = f"{action_url}{'&' if '?' in action_url else '?'}ticket={ticket.id}"
        dispatch(db, event, recipient_user_id,
                 context={"title": title, "message": f"{ticket.ticket_number}: {ticket.subject}",
                          "action_url": deep},
                 audience="SUPPORT")
    except Exception:
        pass
