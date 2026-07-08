"""Support Desk — the escalation engine ("Thermal Updraft" desk).

One writer (`apply_escalation`) shared by the escalate route, bulk escalate, the vendor
OLA sweep and the SLA-breach auto-escalation sweep, so every path that raises a tier
writes the SAME structured record (level, type, coded reason, target team, ack clock)
and the SAME `escalated` activity — the escalation-history endpoint derives the tier
timeline from those activities, so parity here is what makes the history complete.

Two sweeps live here too (mirrors ``vendor.py``):
  • ``sweep_sla_breach_escalation`` — auto-escalates SLA-RESOLUTION-breached, owned,
    non-terminal tickets EXACTLY ONCE (stamped via ``auto_escalated_at``). Response
    breaches deliberately do NOT auto-escalate (see AUTO_ESCALATE_BREACH_KINDS) — they
    surface as the "breach candidates" lens instead.
  • ``sweep_escalation_response_overdue`` — nudges owners whose escalation sat unacked
    past its response deadline (day-throttled via the activity log, like the war-room
    update-overdue sweep).

No router imports here (import-cycle-free); notification goes through the local
``_notify_safe`` mirror in ``_common``.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_

from app.models.support_desk.ticket import SdTicket, SdTicketActivity, SdTicketComment
from app.models.support_desk.constants import (
    TicketStatus, CommentAuthorKind,
    OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES,
    EscalationType, ESCALATION_RESPONSE_MINUTES, ESCALATION_RESPONSE_DEFAULT_MINUTES,
    AUTO_ESCALATE_BREACH_KINDS,
    EVT_TICKET_ESCALATED, EVT_TICKET_STATUS,
)
from app.utils.support_desk import sla as sla_util


def apply_escalation(db, t: SdTicket, actor_id, actor_name: str, *,
                     reason: str | None = None, reason_code: str | None = None,
                     escalation_type: str | None = None, to_team_id=None,
                     response_minutes: int | None = None, auto: bool = False,
                     change_status: bool = True, now=None) -> None:
    """Raise one tier and write the full structured record + the `escalated` activity.

    The caller owns guards (terminal / owner), any internal comment, notify and commit —
    this is the single source of truth for WHAT an escalation writes:
      level++ · is_escalated · escalated_at/by · type · reason(+code) · target team ·
      esc-ACK CLEARED (each tier demands a fresh acknowledgement) · response clock armed ·
      status→ESCALATED when still in the open set (WITH stop-the-clock bookkeeping — the
      old inline path skipped it, freezing the SLA forever when escalating a paused ticket).

    ``change_status=False`` keeps the current status (the vendor sweep escalates
    visibility while the ticket stays PENDING_VENDOR so the customer SLA remains paused).
    """
    now = now or sla_util.now_utc()
    t.is_escalated = True
    t.escalation_level = (t.escalation_level or 0) + 1
    t.escalated_at = now
    t.escalated_by_id = actor_id
    t.escalation_type = escalation_type or EscalationType.HIERARCHICAL.value
    if reason_code:
        t.escalation_reason_code = reason_code
    if reason:
        t.escalation_reason = reason
    if to_team_id:
        t.escalated_to_team_id = to_team_id
    # A new tier lift voids the previous tier's acknowledgement and re-arms the clock.
    t.escalation_acknowledged_at = None
    t.escalation_acknowledged_by_id = None
    minutes = response_minutes or ESCALATION_RESPONSE_MINUTES.get(
        t.priority, ESCALATION_RESPONSE_DEFAULT_MINUTES)
    t.escalation_response_due_at = now + timedelta(minutes=minutes)
    if change_status and t.status in OPEN_TICKET_STATUSES and t.status != TicketStatus.ESCALATED.value:
        old = t.status
        # Leaving a pause state must un-freeze the SLA clock (extend deadlines by the
        # paused duration) exactly like _transition_status does.
        sla_util.apply_pause_transition(t, old, TicketStatus.ESCALATED.value, now)
        t.status = TicketStatus.ESCALATED.value
        sla_util.recompute_breach_flags(t, now)
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=actor_id, actor_name=actor_name or "System",
        action="escalated",
        detail={
            "level": t.escalation_level,
            "reason": reason,
            "reason_code": reason_code,
            "escalation_type": t.escalation_type,
            "to_team_id": str(to_team_id) if to_team_id else None,
            "auto": bool(auto),
            "response_due_at": t.escalation_response_due_at.isoformat(),
        },
    ))


def sweep_sla_breach_escalation(db, *, team_cond=None, cap: int = 500) -> int:
    """Auto-escalate SLA-breached tickets EXACTLY ONCE each (ServiceNow-style rule).

    Targets: not deleted, ACTIVELY WORKED (open|in_progress only — paused/parked tickets
    are deliberately excluded: their clock is stopped and auto-lifting an on-hold ticket
    would bypass the hold bookkeeping), owned, NOT already escalated, never auto-escalated
    before (``auto_escalated_at`` is the once-only stamp), and breached on a kind listed in
    AUTO_ESCALATE_BREACH_KINDS (resolution-only by default). Unowned breached tickets are
    left for the "breach candidates" lens — the escalate guard requires an owner and the
    sweep must not invent one. The caller commits. Returns how many were raised.
    """
    from app.routers.support_desk._common import _notify_safe
    now = sla_util.now_utc()
    breach_conds = []
    if "resolution" in AUTO_ESCALATE_BREACH_KINDS:
        breach_conds.append(SdTicket.sla_resolution_breached == True)  # noqa: E712
    if "response" in AUTO_ESCALATE_BREACH_KINDS:
        breach_conds.append(SdTicket.sla_response_breached == True)  # noqa: E712
    if not breach_conds:
        return 0
    q = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.status.in_([TicketStatus.OPEN.value, TicketStatus.IN_PROGRESS.value]),
        SdTicket.is_escalated == False,  # noqa: E712
        SdTicket.auto_escalated_at.is_(None),
        SdTicket.assigned_agent_id.isnot(None),
        or_(*breach_conds),
    )
    if team_cond is not None:
        q = q.filter(team_cond)
    rows = q.limit(cap).all()
    n = 0
    for t in rows:
        t.auto_escalated_at = now
        reason = "SLA resolution deadline breached — auto-escalated"
        apply_escalation(
            db, t, None, "System",
            reason=reason, reason_code="sla_breach",
            escalation_type=EscalationType.HIERARCHICAL.value, auto=True, now=now)
        db.add(SdTicketComment(
            ticket_id=t.id, author_user_id=None, author_name="System",
            author_kind=CommentAuthorKind.SYSTEM.value,
            body=f"[Escalation] {reason}", is_internal=True))
        _notify_safe(db, EVT_TICKET_ESCALATED, t.assigned_agent_id, t,
                     title=f"Auto-escalated (L{t.escalation_level}) — {t.ticket_number} breached its SLA",
                     action_url="/user/support/tickets/escalated")
        n += 1
    return n


def sweep_escalation_response_overdue(db) -> int:
    """Escalation ack-clock sweep — an active escalation whose response deadline lapsed
    UNACKNOWLEDGED gets an ``escalation_response_overdue`` timeline entry + a nudge to
    its owner. At most one nudge per day per ticket (deduped on the last such activity —
    same throttle as the war-room update-overdue sweep). Does NOT clear the deadline:
    the board keeps showing OVERDUE until the receiving tier actually acks. Commits when
    it nudged anything. Returns the count nudged."""
    from app.routers.support_desk._common import _notify_safe
    nowt = sla_util.now_utc()
    day_ago = nowt - timedelta(days=1)
    rows = (db.query(SdTicket)
            .filter(SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)),
                    SdTicket.is_escalated == True,  # noqa: E712
                    SdTicket.escalation_acknowledged_at.is_(None),
                    SdTicket.escalation_response_due_at.isnot(None),
                    SdTicket.escalation_response_due_at < nowt).all())
    if not rows:
        return 0
    recent = {str(r[0]) for r in (db.query(SdTicketActivity.ticket_id)
              .filter(SdTicketActivity.ticket_id.in_([t.id for t in rows]),
                      SdTicketActivity.action == "escalation_response_overdue",
                      SdTicketActivity.created_at > day_ago).all())}
    n = 0
    for t in rows:
        if str(t.id) in recent:
            continue  # already nudged today
        overdue_min = max(0, int((nowt - sla_util._aware(t.escalation_response_due_at)).total_seconds() // 60))
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System",
            action="escalation_response_overdue",
            detail={"auto": True, "overdue_minutes": overdue_min, "level": t.escalation_level}))
        _notify_safe(db, EVT_TICKET_STATUS, t.assigned_agent_id, t,
                     title=f"Escalation response overdue — {t.ticket_number} (L{t.escalation_level}, {overdue_min}m past)",
                     action_url="/user/support/tickets/escalated")
        n += 1
    if n:
        db.commit()
    return n
