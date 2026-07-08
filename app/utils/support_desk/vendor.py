"""Support Desk — pending-vendor overdue sweep (auto-escalate on vendor OLA breach).

When a ticket handed off to a third-party vendor blows past its expected-return date
(``vendor_due_at``), the desk shouldn't let it rot silently just because the CUSTOMER
SLA is paused. This sweep finds overdue, not-yet-flagged PENDING_VENDOR tickets and:
  • stamps ``vendor_overdue_flagged`` so it fires EXACTLY ONCE per hand-off (idempotent),
  • writes a ``vendor_overdue`` activity, and
  • (optionally) raises escalation visibility WITHOUT changing status — the ticket stays
    PENDING_VENDOR so the customer SLA remains paused; only ``is_escalated`` + the level
    are bumped so it also surfaces on the Escalated desk.

Safe to call opportunistically on list-load (cheap, guarded, single UPDATE-ish pass) and
from the scheduled ``tasks_cron.py``.
"""
from __future__ import annotations

from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.constants import TicketStatus
from app.utils.support_desk import sla as sla_util


def sweep_vendor_overdue(db, *, team_cond=None, escalate: bool = True, cap: int = 500) -> int:
    """Flag + optionally escalate overdue vendor hand-offs. Returns how many were flagged.

    ``team_cond`` (optional) is a SQLAlchemy condition to restrict the sweep to an agent's
    team purview (the same seal the list uses). The caller commits.
    """
    now = sla_util.now_utc()
    q = db.query(SdTicket).filter(
        SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.status == TicketStatus.PENDING_VENDOR.value,
        SdTicket.vendor_overdue_flagged == False,  # noqa: E712
        SdTicket.vendor_due_at.isnot(None),
        SdTicket.vendor_due_at < now,
    )
    if team_cond is not None:
        q = q.filter(team_cond)
    rows = q.limit(cap).all()
    flagged = 0
    for t in rows:
        t.vendor_overdue_flagged = True
        detail = {"vendor": t.vendor_name, "due_at": t.vendor_due_at.isoformat() if t.vendor_due_at else None}
        if escalate and not t.is_escalated:
            # Route through the shared escalation engine so vendor auto-escalations carry
            # the same structured record + `escalated` activity as every other path (the
            # tier-history endpoint derives from those activities — parity matters).
            from app.utils.support_desk.escalation import apply_escalation
            apply_escalation(
                db, t, None, "System",
                reason=f"Vendor overdue — {t.vendor_name or 'third party'} past expected-return date",
                reason_code="vendor_stall", auto=True, change_status=False, now=now)
            detail["escalated"] = True
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="System",
            action="vendor_overdue", detail=detail,
        ))
        flagged += 1
    return flagged
