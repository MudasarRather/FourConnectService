"""Support Desk — SLA clock helpers.

Deadlines are derived from an SLA package's priority matrix at ticket creation;
display states are computed on read. Pure functions — no DB writes here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.support_desk.constants import SLA_PAUSE_STATUSES


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _eff_ref(ticket, ref: datetime | None) -> datetime:
    """The reference instant for SLA evaluation. While a ticket is paused (stop-the-clock),
    the clock is FROZEN at the moment it was paused — so state/breach are measured as of
    ``sla_paused_since`` rather than the live wall clock. Off-pause it's the given ref/now."""
    since = _aware(getattr(ticket, "sla_paused_since", None))
    if since is not None:
        return since
    return ref or now_utc()


def apply_pause_transition(ticket, old_status: str, new_status: str, ref: datetime | None = None) -> None:
    """Stop-the-clock accounting, called on EVERY status change (before resolved_at is
    stamped). Entering a pause state freezes the clock (records ``sla_paused_since``);
    leaving one pushes the still-open response/resolution deadlines out by the paused
    duration — so all downstream SQL filters, breach flags and displays stay correct with
    no read-time math — and banks the paused time in ``sla_paused_ms`` for reporting.
    A pause→pause move (e.g. Pending Customer → On Hold) keeps the clock frozen throughout."""
    ref = ref or now_utc()
    was = old_status in SLA_PAUSE_STATUSES
    now_paused = new_status in SLA_PAUSE_STATUSES
    if now_paused and not was:
        if getattr(ticket, "sla_paused_since", None) is None:
            ticket.sla_paused_since = ref
    elif was and not now_paused:
        since = _aware(getattr(ticket, "sla_paused_since", None))
        if since is not None:
            delta = ref - since
            secs = delta.total_seconds()
            if secs > 0:
                # extend the LIVE deadlines by the paused span (only while still un-met)
                rsd = _aware(getattr(ticket, "resolution_due_at", None))
                if rsd is not None and getattr(ticket, "resolved_at", None) is None:
                    ticket.resolution_due_at = rsd + delta
                rd = _aware(getattr(ticket, "response_due_at", None))
                if rd is not None and getattr(ticket, "first_responded_at", None) is None:
                    ticket.response_due_at = rd + delta
                ticket.sla_paused_ms = int(getattr(ticket, "sla_paused_ms", 0) or 0) + int(secs * 1000)
        ticket.sla_paused_since = None


def compute_deadlines(package, priority: str, start: datetime | None = None):
    """Return (response_due_at, resolution_due_at) for a priority on a package.

    ``package`` is an SdSlaPackage (or None). Missing rows → (None, None) so the
    ticket simply has no SLA clock rather than erroring.
    """
    start = start or now_utc()
    matrix = getattr(package, "matrix", None) or {}
    row = matrix.get(priority) or matrix.get(str(priority)) or {}
    resp = row.get("response_mins")
    reso = row.get("resolution_mins")
    response_due = start + timedelta(minutes=int(resp)) if resp else None
    resolution_due = start + timedelta(minutes=int(reso)) if reso else None
    return response_due, resolution_due


def response_state(ticket, ref: datetime | None = None) -> str | None:
    """ok | due-soon | breached | met | None."""
    due = _aware(getattr(ticket, "response_due_at", None))
    if due is None:
        return None
    responded = _aware(getattr(ticket, "first_responded_at", None))
    if responded is not None:
        return "met" if responded <= due else "breached"
    ref = _eff_ref(ticket, ref)   # frozen while paused (stop-the-clock)
    if ref > due:
        return "breached"
    # within 15% of the window (or <30 min) → due-soon
    remaining = (due - ref).total_seconds()
    return "due-soon" if remaining <= max(1800, 0.15 * 86400) and remaining <= 7200 else "ok"


def resolution_state(ticket, ref: datetime | None = None) -> str | None:
    due = _aware(getattr(ticket, "resolution_due_at", None))
    if due is None:
        return None
    resolved = _aware(getattr(ticket, "resolved_at", None))
    if resolved is not None:
        return "met" if resolved <= due else "breached"
    ref = _eff_ref(ticket, ref)   # frozen while paused (stop-the-clock)
    if ref > due:
        return "breached"
    remaining = (due - ref).total_seconds()
    return "due-soon" if remaining <= 7200 else "ok"


def recompute_breach_flags(ticket, ref: datetime | None = None) -> None:
    """Set sla_*_breached flags in-place based on current time (best-effort). While a ticket
    is paused the clock is frozen (evaluated as of sla_paused_since), so a pause can never
    tip an un-breached ticket into breach.

    Also maintains the breach-detection stamps (``sla_*_breached_at``, Breached desk):
    a flag flipping True stamps the DUE instant (honest aging — the breach happened when
    the target passed, not when we noticed); a flip back to False (deadline pushed out on
    pause-resume) clears the stamp so aging never lies. Field mutation only — no DB writes."""
    ref = _eff_ref(ticket, ref)
    rd = _aware(getattr(ticket, "response_due_at", None))
    resp = _aware(getattr(ticket, "first_responded_at", None))
    if rd is not None:
        breached = bool((resp or ref) > rd)
        ticket.sla_response_breached = breached
        if breached:
            if getattr(ticket, "sla_response_breached_at", None) is None:
                ticket.sla_response_breached_at = rd
        else:
            ticket.sla_response_breached_at = None
    rsd = _aware(getattr(ticket, "resolution_due_at", None))
    res = _aware(getattr(ticket, "resolved_at", None))
    if rsd is not None:
        breached = bool((res or ref) > rsd)
        ticket.sla_resolution_breached = breached
        if breached:
            if getattr(ticket, "sla_resolution_breached_at", None) is None:
                ticket.sla_resolution_breached_at = rsd
        else:
            ticket.sla_resolution_breached_at = None
