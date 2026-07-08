"""Support Desk — shared router helpers: ticket numbering, SLA-package
resolution, and batched enrichment of ticket responses (display names + SLA
states + comment counts) so admin/self/public routers stay DRY and N+1-free.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Iterable

from sqlalchemy import func, and_, or_, case
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.support_desk.core import SdOrganization, SdCustomer, SdCategory, SdSlaPackage
from app.models.support_desk.ticket import SdTicket, SdTicketComment, SdTicketActivity
from app.models.support_desk.workspace import SdTeam
from app.models.support_desk.constants import (
    TicketStatus, SUPPORT_RESOLVED_AUTOCLOSE_DAYS,
    PENDING_AUTO_CLOSE_DAYS, PENDING_WARN_DAYS, ResolutionCode,
    OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES, STALE_HOLD_DAYS,
    EVT_TICKET_STATUS, EVT_TICKET_RESOLVED, EVT_TICKET_REOPENED,
    ReopenSource, REOPEN_SOURCES, REOPEN_REASON_CODES, CommentAuthorKind,
    ArchiveReason, SUPPORT_CLOSED_AUTOARCHIVE_DAYS, SUPPORT_ARCHIVE_RETENTION_DAYS,
)
from app.utils.support_desk import sla as sla_util


def _notify_safe(db: Session, event: str, recipient_user_id, ticket: SdTicket, *, title: str, action_url: str):
    """Local (import-cycle-free) mirror of tickets.dispatch_safe for the sweep/reactivation
    helpers — routing through the HR notify matrix, deep-linking to the ticket, never fatal."""
    if not recipient_user_id:
        return
    try:
        from app.utils.hr.notify import dispatch
        deep = f"{action_url}{'&' if '?' in action_url else '?'}ticket={ticket.id}"
        dispatch(db, event, recipient_user_id,
                 context={"title": title, "message": f"{ticket.ticket_number}: {ticket.subject}", "action_url": deep},
                 audience="SUPPORT")
    except Exception:
        pass


def _as_uuid(v):
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except Exception:
        return None


def apply_overdue_scope(query, kind: str | None):
    """scope=overdue with the `overdue_kind` refinement (any|response|resolution).
    None/'resolution' keeps the legacy semantics — past the RESOLUTION due date.
    'response' → past the response due date with NO first reply; 'any' → either clock.
    Every variant requires an OPEN status and a RUNNING clock (paused = frozen, not
    overdue — the deadline is pushed out on resume). Shared by the admin list, the
    self list and the command-center list so all three panels agree."""
    nowt = sla_util.now_utc()
    res_over = and_(SdTicket.resolution_due_at.isnot(None),
                    SdTicket.resolution_due_at < nowt)
    resp_over = and_(SdTicket.first_responded_at.is_(None),
                     SdTicket.response_due_at.isnot(None),
                     SdTicket.response_due_at < nowt)
    k = (kind or "resolution").lower()
    cond = resp_over if k == "response" else (or_(res_over, resp_over) if k == "any" else res_over)
    return query.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES),
                        SdTicket.sla_paused_since.is_(None),
                        cond)


def generate_ticket_number(db: Session) -> str:
    """Prefer the configured NumberingSeries; fall back to a TKT+hex id."""
    try:
        from app.utils.hr.numbering import next_number
        n = next_number(db, "SUPPORT_TICKET")
        if n:
            return n
    except Exception:
        pass
    return f"TKT{uuid.uuid4().hex[:8].upper()}"


def resolve_sla_package(db: Session, explicit_id=None, organization_id=None) -> SdSlaPackage | None:
    """explicit package > organization's package > default package."""
    if explicit_id:
        pkg = db.query(SdSlaPackage).filter(
            SdSlaPackage.id == explicit_id, SdSlaPackage.is_deleted == False  # noqa: E712
        ).first()
        if pkg:
            return pkg
    if organization_id:
        org = db.query(SdOrganization).filter(SdOrganization.id == organization_id).first()
        if org and org.sla_package_id:
            pkg = db.query(SdSlaPackage).filter(SdSlaPackage.id == org.sla_package_id).first()
            if pkg:
                return pkg
    return db.query(SdSlaPackage).filter(
        SdSlaPackage.is_default == True, SdSlaPackage.is_deleted == False  # noqa: E712
    ).first()


def _user_names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = db.query(User.id, User.full_name).filter(User.id.in_(ids)).all()
    return {str(r[0]): r[1] for r in rows}


def enrich_tickets(db: Session, tickets: Iterable[SdTicket]) -> list[SdTicket]:
    """Attach display names, SLA states and comment counts onto each ticket
    instance (read by Pydantic ``from_attributes``). Batched lookups."""
    tickets = list(tickets)
    if not tickets:
        return tickets

    org_ids = {t.organization_id for t in tickets if t.organization_id}
    cust_ids = {t.customer_id for t in tickets if t.customer_id}
    cat_ids = {t.category_id for t in tickets if t.category_id} | {t.subcategory_id for t in tickets if getattr(t, "subcategory_id", None)}
    team_ids = ({t.team_id for t in tickets if t.team_id}
                | {t.escalated_to_team_id for t in tickets if getattr(t, "escalated_to_team_id", None)})
    user_ids = set()
    for t in tickets:
        user_ids.update([t.assigned_agent_id, t.raised_by_user_id, getattr(t, "assigned_engineer_id", None),
                         getattr(t, "acknowledged_by_id", None), getattr(t, "escalated_by_id", None),
                         getattr(t, "escalation_acknowledged_by_id", None),
                         getattr(t, "last_reopened_by_id", None),
                         getattr(t, "resolved_by_id", None),
                         getattr(t, "closed_by_id", None),
                         getattr(t, "archived_by_id", None)])
        for c in (t.collaborators or []):
            cu = _as_uuid(c)
            if cu:
                user_ids.add(cu)

    orgs = {str(o.id): o.name for o in db.query(SdOrganization.id, SdOrganization.name)
            .filter(SdOrganization.id.in_(org_ids)).all()} if org_ids else {}
    custs = {str(c.id): c.name for c in db.query(SdCustomer.id, SdCustomer.name)
             .filter(SdCustomer.id.in_(cust_ids)).all()} if cust_ids else {}
    cats = {str(c.id): c.name for c in db.query(SdCategory.id, SdCategory.name)
            .filter(SdCategory.id.in_(cat_ids)).all()} if cat_ids else {}
    teams = {str(tm.id): tm.name for tm in db.query(SdTeam.id, SdTeam.name)
             .filter(SdTeam.id.in_(team_ids)).all()} if team_ids else {}
    users = _user_names(db, user_ids)

    # Follow-up linkage (Closed desk): human ticket number of the sealed original,
    # batched in one pass so list rows can render the chain chip without an N+1.
    fu_ids = {getattr(t, "follow_up_of_id", None) for t in tickets if getattr(t, "follow_up_of_id", None)}
    fu_nums = ({str(r[0]): r[1] for r in
                db.query(SdTicket.id, SdTicket.ticket_number)
                .filter(SdTicket.id.in_(fu_ids)).all()} if fu_ids else {})

    counts = {}
    staff_public = {}
    tids = [t.id for t in tickets]
    if tids:
        # One grouped pass gets both the total AND the public-staff-touch count — the
        # latter powers the Resolved desk's FCR ("one-touch") lens: a genuine one-touch
        # resolve carries 0-1 public staff comments (the resolution reply itself).
        rows = (db.query(SdTicketComment.ticket_id, func.count(SdTicketComment.id),
                         func.sum(case((and_(SdTicketComment.is_internal == False,  # noqa: E712
                                             SdTicketComment.author_kind == CommentAuthorKind.STAFF.value), 1),
                                       else_=0)))
                .filter(SdTicketComment.ticket_id.in_(tids))
                .group_by(SdTicketComment.ticket_id).all())
        counts = {str(r[0]): r[1] for r in rows}
        staff_public = {str(r[0]): int(r[2] or 0) for r in rows}

    nowt = sla_util.now_utc()
    for t in tickets:
        t.organization_name = orgs.get(str(t.organization_id)) if t.organization_id else None
        t.customer_name = custs.get(str(t.customer_id)) if t.customer_id else None
        t.category_name = cats.get(str(t.category_id)) if t.category_id else None
        t.subcategory_name = cats.get(str(t.subcategory_id)) if getattr(t, "subcategory_id", None) else None
        t.assigned_agent_name = users.get(str(t.assigned_agent_id)) if t.assigned_agent_id else None
        t.raised_by_name = users.get(str(t.raised_by_user_id)) if t.raised_by_user_id else None
        t.team_name = teams.get(str(t.team_id)) if t.team_id else None
        t.collaborator_people = [{"id": str(c), "name": users.get(str(c)) or "Member"}
                                 for c in (t.collaborators or [])]
        t.sla_response_state = sla_util.response_state(t)
        t.sla_resolution_state = sla_util.resolution_state(t)
        t.comment_count = counts.get(str(t.id), 0)
        t.agent_public_comment_count = staff_public.get(str(t.id), 0)
        # Pending-customer telemetry (drives the "Silence Chronometer" countdown on the desk).
        # Silence is measured from when the ticket was paused (entered pending) or the last
        # customer reply — NOT updated_at (a reminder bumps that), so it stays stable.
        t.pending_since = sla_util._aware(getattr(t, "sla_paused_since", None))
        t.silence_ms = None
        t.auto_close_at = None
        if t.status == TicketStatus.PENDING_CUSTOMER.value:
            ref = (t.pending_since or sla_util._aware(getattr(t, "last_customer_reply_at", None))
                   or sla_util._aware(getattr(t, "updated_at", None)))
            if ref is not None:
                t.silence_ms = max(0, int((nowt - ref).total_seconds() * 1000))
                t.auto_close_at = ref + timedelta(days=PENDING_AUTO_CLOSE_DAYS)
        # Resolved telemetry (drives the Resolved desk's auto-close countdown + reopen
        # window): a resolved ticket closes SUPPORT_RESOLVED_AUTOCLOSE_DAYS after
        # resolved_at — the same window inside which the requester/portal may reopen.
        elif t.status == TicketStatus.RESOLVED.value:
            rref = sla_util._aware(getattr(t, "resolved_at", None))
            if rref is not None:
                t.auto_close_at = rref + timedelta(days=SUPPORT_RESOLVED_AUTOCLOSE_DAYS)
        t.resolved_by_name = (users.get(str(t.resolved_by_id))
                              if getattr(t, "resolved_by_id", None) else None)
        # Closed-desk telemetry: who sealed the record (None = the auto-close sweep)
        # + the human number of the ticket this one follows up on.
        t.closed_by_name = (users.get(str(t.closed_by_id))
                            if getattr(t, "closed_by_id", None) else None)
        t.follow_up_of_number = (fu_nums.get(str(t.follow_up_of_id))
                                 if getattr(t, "follow_up_of_id", None) else None)
        # Pending-vendor telemetry (drives the "Vendor Relay Station" queue). Wait is measured
        # from the current hand-off (vendor_dispatched_at), falling back to the pause instant.
        t.vendor_wait_ms = None
        t.vendor_overdue = None
        t.vendor_coordinator_name = (users.get(str(t.assigned_engineer_id))
                                     if getattr(t, "assigned_engineer_id", None) else None)
        if t.status == TicketStatus.PENDING_VENDOR.value:
            vref = (sla_util._aware(getattr(t, "vendor_dispatched_at", None))
                    or t.pending_since or sla_util._aware(getattr(t, "updated_at", None)))
            if vref is not None:
                t.vendor_wait_ms = max(0, int((nowt - vref).total_seconds() * 1000))
            due = sla_util._aware(getattr(t, "vendor_due_at", None))
            t.vendor_overdue = bool(due and nowt > due)
        # On-hold telemetry (drives the "Suspension Dock" board). Held time is measured from
        # held_at (falling back to the pause instant); auto_resume_at mirrors hold_until (the
        # expiry sweep releases it then); hold_stale flags holds that sailed past
        # STALE_HOLD_DAYS without a hold review (extend/re-confirm) AND have no release date.
        t.time_on_hold_ms = None
        t.auto_resume_at = None
        t.hold_stale = None
        if t.status == TicketStatus.ON_HOLD.value:
            href = (sla_util._aware(getattr(t, "held_at", None))
                    or t.pending_since or sla_util._aware(getattr(t, "updated_at", None)))
            if href is not None:
                t.time_on_hold_ms = max(0, int((nowt - href).total_seconds() * 1000))
            t.auto_resume_at = sla_util._aware(getattr(t, "hold_until", None))
            review_ref = sla_util._aware(getattr(t, "last_hold_review_at", None)) or href
            t.hold_stale = bool(
                t.auto_resume_at is None and review_ref is not None
                and (nowt - review_ref) >= timedelta(days=STALE_HOLD_DAYS))
        # Reopen telemetry (drives the "Möbius Loop" Reopened desk).
        t.last_reopened_by_name = (users.get(str(t.last_reopened_by_id))
                                   if getattr(t, "last_reopened_by_id", None) else None)
        # War-room telemetry (drives the Critical board's ACK + update-cadence cells).
        t.acknowledged_by_name = (users.get(str(t.acknowledged_by_id))
                                  if getattr(t, "acknowledged_by_id", None) else None)
        t.update_due_ms = None
        t.update_overdue = None
        next_due = sla_util._aware(getattr(t, "next_update_due_at", None))
        if next_due is not None and t.status not in TERMINAL_TICKET_STATUSES:
            t.update_due_ms = int((next_due - nowt).total_seconds() * 1000)
            t.update_overdue = t.update_due_ms < 0
        # Escalation telemetry (drives the "Thermal Updraft" escalated desk): who raised /
        # received it, the eMTTA ack state, the receiving tier's response clock, and dwell.
        t.escalated_by_name = (users.get(str(t.escalated_by_id))
                               if getattr(t, "escalated_by_id", None) else None)
        t.escalated_to_team_name = (teams.get(str(t.escalated_to_team_id))
                                    if getattr(t, "escalated_to_team_id", None) else None)
        t.escalation_acknowledged_by_name = (users.get(str(t.escalation_acknowledged_by_id))
                                             if getattr(t, "escalation_acknowledged_by_id", None) else None)
        t.escalation_acked = None
        t.esc_response_due_ms = None
        t.esc_response_overdue = None
        t.time_since_escalated_ms = None
        t.auto_escalated = None
        if t.is_escalated:
            t.escalation_acked = bool(getattr(t, "escalation_acknowledged_at", None))
            t.auto_escalated = bool(getattr(t, "auto_escalated_at", None))
            esc_at = sla_util._aware(getattr(t, "escalated_at", None))
            if esc_at is not None:
                t.time_since_escalated_ms = max(0, int((nowt - esc_at).total_seconds() * 1000))
            due = sla_util._aware(getattr(t, "escalation_response_due_at", None))
            if due is not None and not t.escalation_acked and t.status not in TERMINAL_TICKET_STATUSES:
                t.esc_response_due_ms = int((due - nowt).total_seconds() * 1000)
                t.esc_response_overdue = t.esc_response_due_ms < 0
        # Deep-storage telemetry (drives the Archived desk): who shelved the record, when
        # its retention window ends, and whether it is purge-eligible RIGHT NOW. Legal-hold
        # rows never become eligible — the countdown is suspended, not running.
        t.archived_by_name = (users.get(str(t.archived_by_id))
                              if getattr(t, "archived_by_id", None) else None)
        t.purge_eligible_at = None
        t.purge_eligible = None
        if getattr(t, "is_deleted", False):
            arch_at = sla_util._aware(getattr(t, "archived_at", None))
            if arch_at is not None and not getattr(t, "legal_hold", False):
                t.purge_eligible_at = arch_at + timedelta(days=SUPPORT_ARCHIVE_RETENTION_DAYS)
                t.purge_eligible = nowt >= t.purge_eligible_at
            else:
                t.purge_eligible = False
    return tickets


def _pending_silence_ref(t: SdTicket):
    """The stable instant a pending-customer ticket's silence is measured from — pause entry,
    else last customer reply, else last update. Reminders don't move it (they bump updated_at
    but not sla_paused_since/last_customer_reply_at)."""
    return (sla_util._aware(getattr(t, "sla_paused_since", None))
            or sla_util._aware(getattr(t, "last_customer_reply_at", None))
            or sla_util._aware(getattr(t, "updated_at", None)))


def reactivate_on_customer_reply(db: Session, t: SdTicket, nowt=None) -> str | None:
    """Loophole fix: when a customer replies to a ticket that is AWAITING them, the ball is
    back in the desk's court — pull it out of pending_customer into active work so it re-enters
    the agent's queue (SLA un-freezes via apply_pause_transition). No-op for any other status.
    Caller adds the customer comment + commits; returns the new status or None."""
    if not t or t.status != TicketStatus.PENDING_CUSTOMER.value:
        return None
    nowt = nowt or sla_util.now_utc()
    target = TicketStatus.IN_PROGRESS.value if t.assigned_agent_id else TicketStatus.OPEN.value
    old = t.status
    sla_util.apply_pause_transition(t, old, target, nowt)   # unfreeze + push out deadlines
    t.status = target
    sla_util.recompute_breach_flags(t, nowt)
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=None, actor_name="System", action="status_changed",
        detail={"from": old, "to": target, "auto": True, "reason": "customer_replied"}))
    return target


def apply_close_source(query, source: str | None):
    """Closed-desk refinement: narrow to one closure-source bucket. Buckets are mutually
    exclusive and mirror closed_stats' split exactly (merged > withdrawn > no_response >
    auto_sweep > manual) so a lens click always paginates the same population the stat
    counted. Unknown/blank values are a no-op."""
    if not source:
        return query
    not_merged = SdTicket.merged_into_id.is_(None)
    plain = and_(not_merged,
                 or_(SdTicket.resolution_code.is_(None),
                     SdTicket.resolution_code.notin_((ResolutionCode.CANCELLED.value,
                                                      ResolutionCode.NO_RESPONSE.value))))
    conds = {
        "merged": SdTicket.merged_into_id.isnot(None),
        "withdrawn": and_(not_merged, SdTicket.resolution_code == ResolutionCode.CANCELLED.value),
        "no_response": and_(not_merged, SdTicket.resolution_code == ResolutionCode.NO_RESPONSE.value),
        "auto_sweep": and_(plain, SdTicket.closed_by_id.is_(None)),
        "manual": and_(plain, SdTicket.closed_by_id.isnot(None)),
    }
    cond = conds.get(source)
    return query.filter(cond) if cond is not None else query


def require_resolution_summary(summary: str | None) -> None:
    """Resolution-notes drop-gate (ServiceNow parity): every resolve/close must say what
    fixed it. The UI has always required this (SdResolveModal/SdCloseModal) — this seals
    the API path so a bare POST can't record an empty resolution. Shared by the admin
    resolve route, bulk resolve/close, and the worker self-resolve."""
    from fastapi import HTTPException
    if not summary or len(summary.strip()) < 3:
        raise HTTPException(422, "A resolution summary is required — say what fixed it.")


def apply_reopen(db: Session, t: SdTicket, actor_id, actor_name, *,
                 source: str, reason: str | None = None, reason_code: str | None = None,
                 nowt=None) -> None:
    """The ONE reopen bookkeeping engine (single-writer rule). Shared by the agent
    transition (tickets._transition_status — reopen route, bulk, board moves), the
    requester self-reopen, and the portal auto-reopen, so the cycle record can never
    drift between paths. The caller sets the new status and commits.

    Stamps, in order:
      1. Failed-fix snapshot: prev_resolution_code/summary + prev_resolved_at, and
         reopen_latency_ms = the resolved→reopen gap (the time-to-reopen KPI).
      2. The reopen record: reopened_count++, last_reopened_at/by, source (validated
         against ReopenSource), free-text reason + coded verdict (ReopenReason).
      3. A fresh cycle: live resolution fields + terminal stamps cleared. CSAT is KEPT —
         it is the customer's historic verdict on the failed fix.
      4. A fresh re-resolution SLA cycle: resolution_due_at re-armed from NOW off the
         ticket's SLA package (fallback: the original created→due span). The response
         clock is deliberately NOT re-armed — first response already happened
         (ServiceNow parity). This MUST run before recompute_breach_flags: with the
         future due date the recompute clears the stale active breach flag instead of
         instantly re-flagging the reopened ticket as breached against the OLD deadline
         (the pre-existing "instantly overdue on reopen" loophole). Breach history
         survives on the timeline (sweep activities) and in the audit ledger.
      5. Exactly one 'reopened' activity row (the ONLY writer of that action).
    """
    nowt = nowt or sla_util.now_utc()
    prev_resolved = sla_util._aware(getattr(t, "resolved_at", None))
    # 1. preserve the failed fix
    t.prev_resolution_code = t.resolution_code
    t.prev_resolution_summary = t.resolution_summary
    t.prev_resolved_at = t.resolved_at
    t.reopen_latency_ms = (max(0, int((nowt - prev_resolved).total_seconds() * 1000))
                           if prev_resolved else None)
    # 2. the reopen record
    t.reopened_count = (t.reopened_count or 0) + 1
    t.last_reopened_at = nowt
    t.last_reopened_by_id = actor_id
    t.reopen_source = source if source in REOPEN_SOURCES else ReopenSource.AGENT.value
    t.reopen_reason = reason
    t.reopen_reason_code = reason_code if reason_code in REOPEN_REASON_CODES else None
    # 3. fresh cycle
    t.resolved_at = None
    t.closed_at = None
    t.resolution_code = None
    t.resolution_summary = None
    t.resolution_category = None
    # Attribution travels with the live resolution record — the failed fix's resolver
    # stays recoverable from the activity trail (and the prev_* snapshot keeps the fix).
    t.resolved_by_id = None
    t.closed_by_id = None
    # 4. fresh re-resolution SLA cycle (BEFORE recompute — see docstring)
    new_due = None
    try:
        pkg = resolve_sla_package(db, t.sla_package_id, t.organization_id)
        _, new_due = sla_util.compute_deadlines(pkg, t.priority, start=nowt)
    except Exception:  # noqa: BLE001 — a missing/broken package must not block a reopen
        new_due = None
    if new_due is None:
        old_due = sla_util._aware(getattr(t, "resolution_due_at", None))
        created = sla_util._aware(getattr(t, "created_at", None))
        if old_due is not None and created is not None and old_due > created:
            new_due = nowt + (old_due - created)
    if new_due is not None:
        t.resolution_due_at = new_due
    t.sla_paused_since = None   # terminal states are never paused; belt-and-braces
    sla_util.recompute_breach_flags(t, nowt)
    # 5. the single 'reopened' activity row. "from" = the terminal status being left
    # (the caller flips t.status AFTER this runs) — it powers the Closed desk's
    # "exhumed from closed" permanence metric, so keep it on every reopen path.
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=actor_id, actor_name=actor_name or "System",
        action="reopened",
        detail={"source": t.reopen_source, "reason": reason,
                "reason_code": t.reopen_reason_code, "latency_ms": t.reopen_latency_ms,
                "cycle": t.reopened_count, "auto": actor_id is None,
                "from": t.status}))


def auto_reopen_on_customer_reply(db: Session, t: SdTicket, actor_name: str | None = None,
                                  nowt=None) -> str | None:
    """Loophole fix (ServiceNow/Zendesk parity): a customer reply to a RESOLVED ticket
    within the reopen window auto-reopens it — the fix evidently didn't hold, and before
    this the reply landed silently while the ticket auto-closed days later. Guards:
      • RESOLVED only — a CLOSED/withdrawn ticket still needs an agent (parity with the
        requester's self-reopen rule);
      • within SUPPORT_RESOLVED_AUTOCLOSE_DAYS of resolved_at — past the window the
        auto-close sweep owns the ticket and a fresh case is the right vehicle.
    Companion of reactivate_on_customer_reply (which only lifts pending_customer).
    Caller adds the reply comment + commits; returns the new status or None."""
    if not t or t.status != TicketStatus.RESOLVED.value:
        return None
    nowt = nowt or sla_util.now_utc()
    resolved = sla_util._aware(getattr(t, "resolved_at", None))
    if resolved is None or (nowt - resolved) > timedelta(days=SUPPORT_RESOLVED_AUTOCLOSE_DAYS):
        return None
    apply_reopen(db, t, None, actor_name or "Customer",
                 source=ReopenSource.PORTAL.value,
                 reason="Customer replied after resolution", nowt=nowt)
    target = (TicketStatus.IN_PROGRESS.value if t.assigned_agent_id
              else TicketStatus.OPEN.value)
    t.status = target
    _notify_safe(db, EVT_TICKET_REOPENED, t.assigned_agent_id, t,
                 title=f"{t.ticket_number} auto-reopened — customer replied after resolution",
                 action_url="/user/support/tickets/reopened")
    return target


def warn_cold_pending(db: Session) -> int:
    """Cron half 1 — nudge requesters whose pending-customer tickets have gone quiet past the
    warning threshold (but not yet the auto-close threshold). At most one nudge per day per
    ticket (throttled on last_reminder_at). Returns the count nudged."""
    nowt = sla_util.now_utc()
    warn_cut = nowt - timedelta(days=PENDING_WARN_DAYS)
    close_cut = nowt - timedelta(days=PENDING_AUTO_CLOSE_DAYS)
    day_ago = nowt - timedelta(days=1)
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status == TicketStatus.PENDING_CUSTOMER.value).all())
    n = 0
    for t in rows:
        ref = _pending_silence_ref(t)
        if ref is None or ref > warn_cut or ref <= close_cut:
            continue  # not yet in the warning window, or already past auto-close
        last = sla_util._aware(getattr(t, "last_reminder_at", None))
        if last is not None and last > day_ago:
            continue  # already nudged today
        t.reminder_count = (t.reminder_count or 0) + 1
        t.last_reminder_at = nowt
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System", action="reminded",
            detail={"count": t.reminder_count, "auto": True, "reason": "pending_customer_warning"}))
        _notify_safe(db, EVT_TICKET_STATUS, t.raised_by_user_id, t,
                     title=f"Reminder — {t.ticket_number} will auto-close soon without your reply",
                     action_url="/user/support/tickets")
        n += 1
    if n:
        db.commit()
    return n


def auto_close_cold_pending(db: Session) -> int:
    """Cron half 2 — auto-resolve pending-customer tickets that went silent past the auto-close
    threshold (resolution_code=no_response). They enter the normal resolved→closed reopen window
    so the requester can still reopen. Idempotent (only touches pending_customer). Returns count."""
    nowt = sla_util.now_utc()
    close_cut = nowt - timedelta(days=PENDING_AUTO_CLOSE_DAYS)
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status == TicketStatus.PENDING_CUSTOMER.value).all())
    n = 0
    for t in rows:
        ref = _pending_silence_ref(t)
        if ref is None or ref > close_cut:
            continue
        old = t.status
        sla_util.apply_pause_transition(t, old, TicketStatus.RESOLVED.value, nowt)  # unfreeze first
        t.resolution_code = ResolutionCode.NO_RESPONSE.value
        t.resolution_summary = (t.resolution_summary
                                or f"Auto-resolved after {PENDING_AUTO_CLOSE_DAYS} days awaiting a customer reply.")
        t.status = TicketStatus.RESOLVED.value
        t.resolved_at = nowt
        sla_util.recompute_breach_flags(t, nowt)
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System", action="resolved",
            detail={"from": old, "to": "resolved", "auto": True, "code": ResolutionCode.NO_RESPONSE.value,
                    "reason": "pending_customer_timeout"}))
        _notify_safe(db, EVT_TICKET_RESOLVED, t.raised_by_user_id, t,
                     title=f"{t.ticket_number} auto-resolved (no reply) — reopen if you still need help",
                     action_url="/user/support/tickets")
        n += 1
    if n:
        db.commit()
    return n


def resume_held_ticket(db: Session, t: SdTicket, nowt=None, *, note: str | None = None) -> str | None:
    """System-side hold release (the sweep's mirror of the /resume route): SLA un-freezes
    (deadlines shift by the paused span), the hold context clears, and the ticket returns to
    held_from_status — falling back to in_progress when it has an owner, else open (never an
    unassigned in_progress). The agent routes go through tickets._transition_status, whose
    on-hold bookkeeping applies the same clearing — keep the two in lockstep.
    Caller commits; returns the new status or None when the ticket isn't on hold."""
    if not t or t.status != TicketStatus.ON_HOLD.value:
        return None
    nowt = nowt or sla_util.now_utc()
    fallback = TicketStatus.IN_PROGRESS.value if t.assigned_agent_id else TicketStatus.OPEN.value
    target = t.held_from_status if t.held_from_status in OPEN_TICKET_STATUSES else fallback
    old = t.status
    sla_util.apply_pause_transition(t, old, target, nowt)   # unfreeze + push out deadlines
    t.hold_reason = None
    t.hold_reason_code = None
    t.hold_until = None
    t.held_at = None
    t.held_from_status = None
    t.last_hold_review_at = None
    t.hold_review_count = 0
    t.status = target
    sla_util.recompute_breach_flags(t, nowt)
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=None, actor_name="System", action="status_changed",
        detail={"from": old, "to": target, "auto": True,
                "note": note or "auto-resumed — hold window expired"}))
    return target


def auto_resume_expired_holds(db: Session) -> int:
    """Hold-expiry sweep — release every on-hold ticket whose hold_until has passed back into
    active work (ServiceNow "on hold until" semantics: the release date actually releases).
    SLA un-freezes via the shared resume path. Safe to call opportunistically on the On-Hold
    list-load AND from tasks_cron. Idempotent (only touches on_hold rows). Returns count."""
    nowt = sla_util.now_utc()
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status == TicketStatus.ON_HOLD.value,
                    SdTicket.hold_until.isnot(None),
                    SdTicket.hold_until < nowt).all())
    n = 0
    for t in rows:
        target = resume_held_ticket(db, t, nowt)
        if not target:
            continue
        _notify_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id or t.raised_by_user_id, t,
                     title=f"{t.ticket_number} auto-resumed — its hold window expired",
                     action_url="/user/support/tickets/on-hold")
        n += 1
    if n:
        db.commit()
    return n


def remind_stale_holds(db: Session) -> int:
    """Stale-hold review sweep — an on-hold ticket with NO release date that hasn't been
    reviewed (held/extended/re-confirmed) in STALE_HOLD_DAYS gets a 'hold review due' timeline
    entry + a nudge to its owner. At most one nudge per day per ticket (deduped on the last
    hold_review_due activity). Never bumps hold_review_count — reminders aren't reviews."""
    nowt = sla_util.now_utc()
    stale_cut = nowt - timedelta(days=STALE_HOLD_DAYS)
    day_ago = nowt - timedelta(days=1)
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status == TicketStatus.ON_HOLD.value,
                    SdTicket.hold_until.is_(None)).all())
    stale = []
    for t in rows:
        ref = (sla_util._aware(getattr(t, "last_hold_review_at", None))
               or sla_util._aware(getattr(t, "held_at", None))
               or sla_util._aware(getattr(t, "updated_at", None)))
        if ref is not None and ref <= stale_cut:
            stale.append(t)
    if not stale:
        return 0
    recent = {str(r[0]) for r in (db.query(SdTicketActivity.ticket_id)
              .filter(SdTicketActivity.ticket_id.in_([t.id for t in stale]),
                      SdTicketActivity.action == "hold_review_due",
                      SdTicketActivity.created_at > day_ago).all())}
    n = 0
    for t in stale:
        if str(t.id) in recent:
            continue  # already nudged today
        days_held = None
        held_ref = sla_util._aware(getattr(t, "held_at", None))
        if held_ref is not None:
            days_held = max(0, int((nowt - held_ref).total_seconds() // 86400))
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System", action="hold_review_due",
            detail={"auto": True, "days_held": days_held, "reviews": t.hold_review_count or 0}))
        _notify_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id, t,
                     title=f"Hold review due — {t.ticket_number} has been parked {days_held or STALE_HOLD_DAYS}+ days",
                     action_url="/user/support/tickets/on-hold")
        n += 1
    if n:
        db.commit()
    return n


def sweep_update_overdue(db: Session) -> int:
    """War-room update-cadence sweep — a non-terminal ticket whose promised stakeholder
    update (next_update_due_at) has lapsed gets an 'update_overdue' timeline entry + a
    nudge to its owner. At most one nudge per day per ticket (deduped on the last
    update_overdue activity). Deliberately does NOT clear next_update_due_at — the board
    keeps showing OVERDUE until an actual status update posts (which re-arms the timer).
    Safe to call on Critical list-load AND from tasks_cron. Returns the count nudged."""
    nowt = sla_util.now_utc()
    day_ago = nowt - timedelta(days=1)
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)),
                    SdTicket.next_update_due_at.isnot(None),
                    SdTicket.next_update_due_at < nowt).all())
    if not rows:
        return 0
    recent = {str(r[0]) for r in (db.query(SdTicketActivity.ticket_id)
              .filter(SdTicketActivity.ticket_id.in_([t.id for t in rows]),
                      SdTicketActivity.action == "update_overdue",
                      SdTicketActivity.created_at > day_ago).all())}
    n = 0
    for t in rows:
        if str(t.id) in recent:
            continue  # already nudged today
        overdue_min = max(0, int((nowt - sla_util._aware(t.next_update_due_at)).total_seconds() // 60))
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System", action="update_overdue",
            detail={"auto": True, "overdue_minutes": overdue_min,
                    "interval_min": t.update_interval_minutes}))
        _notify_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id, t,
                     title=f"Stakeholder update overdue — {t.ticket_number} ({overdue_min}m past its promised update)",
                     action_url="/user/support/tickets/critical")
        n += 1
    if n:
        db.commit()
    return n


def enrich_ticket(db: Session, ticket: SdTicket) -> SdTicket:
    enrich_tickets(db, [ticket])
    return ticket


def maybe_auto_close(db: Session, t: SdTicket) -> bool:
    """Close a RESOLVED ticket once its reopen window (SUPPORT_RESOLVED_AUTOCLOSE_DAYS)
    has elapsed. Idempotent + safe to call on any fetch — the lazy half of the
    resolved→closed workflow (the cron sweep `auto_close_due_tickets` covers the rest).
    Commits when it closes; returns True so the caller re-enriches."""
    if not t or t.status != TicketStatus.RESOLVED.value or not t.resolved_at:
        return False
    nowt = sla_util.now_utc()
    try:
        if (nowt - t.resolved_at) < timedelta(days=SUPPORT_RESOLVED_AUTOCLOSE_DAYS):
            return False
    except TypeError:
        return False  # naive/aware mismatch — leave it for the cron
    t.status = TicketStatus.CLOSED.value
    t.closed_at = nowt
    sla_util.recompute_breach_flags(t, nowt)
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=None, actor_name="System", action="status_changed",
        detail={"from": "resolved", "to": "closed", "auto": True,
                "note": f"Auto-closed after {SUPPORT_RESOLVED_AUTOCLOSE_DAYS} days without reopen"}))
    db.commit()
    db.refresh(t)
    return True


def auto_close_due_tickets(db: Session) -> int:
    """Bulk sweep for the scheduled task — close every resolved ticket past the reopen
    window in one pass. Returns the count closed."""
    nowt = sla_util.now_utc()
    cutoff = nowt - timedelta(days=SUPPORT_RESOLVED_AUTOCLOSE_DAYS)
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status == TicketStatus.RESOLVED.value,
                    SdTicket.resolved_at.isnot(None), SdTicket.resolved_at < cutoff).all())
    n = 0
    for t in rows:
        t.status = TicketStatus.CLOSED.value
        t.closed_at = nowt
        sla_util.recompute_breach_flags(t, nowt)
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System", action="status_changed",
            detail={"from": "resolved", "to": "closed", "auto": True}))
        n += 1
    if n:
        db.commit()
    return n


def auto_archive_old_closed(db: Session) -> int:
    """Retention sweep (Deep Storage desk): move CLOSED records older than
    SUPPORT_CLOSED_AUTOARCHIVE_DAYS into the archive (is_deleted=True) with
    reason_code='auto_retention' — Zendesk-style record lifecycle. Legal-hold rows are
    EXEMPT. archived_by_id stays NULL (= System). No notification (mass event; the
    activity + the Archived desk's chronicle carry the story). Runs lazily when the
    Archived/Closed desks load and as a cron step; idempotent; commits itself.

    NOTE for stats: closed_stats deliberately WIDENS its base to keep counting
    auto_retention tombstones — the Closed desk's lifetime record is not drained by
    this sweep (see tickets_self.closed_stats)."""
    if not SUPPORT_CLOSED_AUTOARCHIVE_DAYS:
        return 0   # sweep disabled by config
    nowt = sla_util.now_utc()
    cutoff = nowt - timedelta(days=SUPPORT_CLOSED_AUTOARCHIVE_DAYS)
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,   # noqa: E712
                    SdTicket.legal_hold == False,   # noqa: E712
                    SdTicket.status == TicketStatus.CLOSED.value,
                    SdTicket.closed_at.isnot(None), SdTicket.closed_at < cutoff).all())
    n = 0
    for t in rows:
        t.is_deleted = True
        t.archived_at = nowt
        t.archived_by_id = None    # System
        t.archive_reason_code = ArchiveReason.AUTO_RETENTION.value
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System", action="archived",
            detail={"auto": True, "reason_code": ArchiveReason.AUTO_RETENTION.value,
                    "note": f"Auto-archived {SUPPORT_CLOSED_AUTOARCHIVE_DAYS} days after close"}))
        n += 1
    if n:
        db.commit()
    return n
