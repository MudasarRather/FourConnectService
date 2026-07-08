"""Support Desk — SLA breach detection sweep (the "Time-Debt Meter" desk).

Closes the stale-flag loophole: ``sla_response_breached`` / ``sla_resolution_breached``
are stored booleans refreshed only on WRITE paths (``recompute_breach_flags``), so an
idle ticket that silently sails past its deadline never used to surface on the Breached
board. This sweep flips the flags for those idle rows, stamps the breach-detection
timestamps (``sla_*_breached_at`` = the DUE instant, honest aging), writes the once-only
``sla_breached`` timeline activity and dispatches EVT_TICKET_SLA_BREACH to the owner.

Runs opportunistically on Breached-board list-load + the breached stats endpoint, and
from ``tasks_cron`` BEFORE the auto-escalation sweep (which consumes these flags — the
ordering lets a freshly-detected breach auto-escalate in the same run).

Mirrors ``escalation.sweep_sla_breach_escalation``: no router imports (import-cycle-free;
notification goes through ``_common._notify_safe``), the CALLER commits, returns the
number of rows flipped. Once-only is structural — the query only selects flag=False rows.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import and_, or_

from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.constants import (
    TERMINAL_TICKET_STATUSES, EVT_TICKET_SLA_BREACH,
)
from app.utils.support_desk import sla as sla_util

# Don't notify owners about breaches older than this at detection time (first deploy /
# long-unattended desks): the timeline activity still lands, only the ping is dropped.
NOTIFY_STALENESS_HOURS = 24


def sweep_sla_breach_flags(db, *, team_cond=None, cap: int = 500) -> int:
    """Flip stale breach flags for idle non-terminal tickets whose deadline has passed.

    Selection: not deleted, non-terminal, clock RUNNING (``sla_paused_since IS NULL`` —
    a paused ticket can never be stale-unbreached because every pause entry is a write
    path that already ran ``recompute_breach_flags``), and either target un-met + past due
    with its flag still False. ``recompute_breach_flags`` does the actual flip so the
    stamp semantics live in exactly one place. Caller commits. Returns rows flipped.
    """
    from app.routers.support_desk._common import _notify_safe
    now = sla_util.now_utc()
    q = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)),
        SdTicket.sla_paused_since.is_(None),
        or_(
            and_(SdTicket.sla_response_breached == False,  # noqa: E712
                 SdTicket.first_responded_at.is_(None),
                 SdTicket.response_due_at.isnot(None),
                 SdTicket.response_due_at < now),
            and_(SdTicket.sla_resolution_breached == False,  # noqa: E712
                 SdTicket.resolved_at.is_(None),
                 SdTicket.resolution_due_at.isnot(None),
                 SdTicket.resolution_due_at < now),
        ),
    )
    if team_cond is not None:
        q = q.filter(team_cond)
    rows = q.limit(cap).all()
    n = 0
    for t in rows:
        was_resp, was_reso = bool(t.sla_response_breached), bool(t.sla_resolution_breached)
        sla_util.recompute_breach_flags(t, now)
        kinds = []
        if t.sla_response_breached and not was_resp:
            kinds.append("response")
        if t.sla_resolution_breached and not was_reso:
            kinds.append("resolution")
        if not kinds:
            continue
        worst_due = sla_util._aware(
            t.resolution_due_at if "resolution" in kinds else t.response_due_at)
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System",
            action="sla_breached",
            detail={
                "auto": True,
                "kinds": kinds,
                "response_due_at": t.response_due_at.isoformat() if t.response_due_at else None,
                "resolution_due_at": t.resolution_due_at.isoformat() if t.resolution_due_at else None,
                "detected_at": now.isoformat(),
            },
        ))
        # Ping the owner only for reasonably fresh breaches (spam throttle on backlogs).
        if t.assigned_agent_id and worst_due is not None and \
                (now - worst_due) <= timedelta(hours=NOTIFY_STALENESS_HOURS):
            kind_label = " + ".join(kinds)
            _notify_safe(db, EVT_TICKET_SLA_BREACH, t.assigned_agent_id, t,
                         title=f"SLA breached ({kind_label}) — {t.ticket_number} is past target",
                         action_url="/user/support/tickets/breached")
        n += 1
    # Backfill safety net: flags already True but never stamped (legacy write-path flips
    # from before the timestamp columns existed). Stamp the due instant; no activity/ping.
    stale = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        or_(and_(SdTicket.sla_response_breached == True,  # noqa: E712
                 SdTicket.sla_response_breached_at.is_(None),
                 SdTicket.response_due_at.isnot(None)),
            and_(SdTicket.sla_resolution_breached == True,  # noqa: E712
                 SdTicket.sla_resolution_breached_at.is_(None),
                 SdTicket.resolution_due_at.isnot(None))),
    ).limit(cap).all()
    for t in stale:
        if t.sla_response_breached and t.sla_response_breached_at is None:
            t.sla_response_breached_at = t.response_due_at
            n += 1
        if t.sla_resolution_breached and t.sla_resolution_breached_at is None:
            t.sla_resolution_breached_at = t.resolution_due_at
            n += 1
    return n
