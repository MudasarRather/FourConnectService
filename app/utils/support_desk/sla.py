"""Support Desk — SLA clock helpers.

Deadlines are derived from an SLA package's priority matrix at ticket creation;
display states are computed on read. Pure functions — no DB writes here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
    ref = ref or now_utc()
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
    ref = ref or now_utc()
    if ref > due:
        return "breached"
    remaining = (due - ref).total_seconds()
    return "due-soon" if remaining <= 7200 else "ok"


def recompute_breach_flags(ticket, ref: datetime | None = None) -> None:
    """Set sla_*_breached flags in-place based on current time (best-effort)."""
    ref = ref or now_utc()
    rd = _aware(getattr(ticket, "response_due_at", None))
    resp = _aware(getattr(ticket, "first_responded_at", None))
    if rd is not None:
        ticket.sla_response_breached = bool((resp or ref) > rd)
    rsd = _aware(getattr(ticket, "resolution_due_at", None))
    res = _aware(getattr(ticket, "resolved_at", None))
    if rsd is not None:
        ticket.sla_resolution_breached = bool((res or ref) > rsd)
