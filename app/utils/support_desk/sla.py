"""Support Desk — SLA clock helpers.

Deadlines are derived from an SLA package's priority matrix at ticket creation;
display states are computed on read. Pure functions — no DB writes here.

Coverage calendars (SdSlaPackage.coverage): with mode="business_hours" the SLA
clock only runs inside the configured window (tz + weekdays + start/end, minus
holidays) — a ticket raised on a Sunday or a holiday starts its clock at the next
covered minute, so weekends/holidays can never breach an 8x5 desk. Empty/{} or
mode="24x7" keeps the legacy wall-clock behaviour; per-priority overrides let a
critical tier stay 24x7 on an otherwise business-hours package. Deadlines stay
ABSOLUTE instants (all downstream SQL filters / sweeps / sorts unchanged) — the
calendar only decides where the deadline lands.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as dtime
from zoneinfo import ZoneInfo

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


# ─────────────────────────── Coverage calendar ───────────────────────────

COVERAGE_MODES = {"24x7", "business_hours"}

# Hard iteration cap for the day-walk: 2 years of consecutive non-covered days means the
# calendar is misconfigured (e.g. every day a holiday) — fall back to wall-clock rather
# than looping forever or stalling the deadline out to infinity.
_MAX_WALK_DAYS = 750


def _parse_hhmm(s, default: dtime) -> dtime:
    try:
        hh, mm = str(s).strip().split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        return default


def resolve_coverage(package, priority: str) -> dict | None:
    """The effective coverage calendar for this (package, priority), or None for 24x7.

    None ⇒ legacy wall-clock math. A business_hours dict is returned only when it is
    actually usable (a real window); degenerate configs (no days, start==end, bad tz)
    fail OPEN to 24x7 — a broken calendar must never freeze the desk's clocks.
    """
    cov = getattr(package, "coverage", None) or {}
    if not isinstance(cov, dict) or not cov:
        return None
    mode = cov.get("mode") or "24x7"
    over = cov.get("priority_overrides") or {}
    if isinstance(over, dict) and priority and over.get(str(priority)) in COVERAGE_MODES:
        mode = over[str(priority)]
    if mode != "business_hours":
        return None
    days = [int(d) for d in (cov.get("days") or []) if str(d).strip().isdigit() and 1 <= int(d) <= 7]
    if not days:
        return None
    start_t = _parse_hhmm(cov.get("start"), dtime(9, 0))
    end_t = _parse_hhmm(cov.get("end"), dtime(18, 0))
    if (end_t.hour, end_t.minute) <= (start_t.hour, start_t.minute):
        return None
    try:
        tz = ZoneInfo(str(cov.get("tz") or "UTC"))
    except Exception:
        tz = timezone.utc
    holidays = {str(h).strip() for h in (cov.get("holidays") or []) if str(h).strip()}
    return {"tz": tz, "days": set(days), "start": start_t, "end": end_t, "holidays": holidays}


def add_covered_minutes(start: datetime, minutes: int, cov: dict) -> datetime:
    """Advance ``minutes`` of COVERED time from ``start`` under a resolved business-hours
    calendar (skipping nights, off-days and holidays), returning an absolute UTC instant.

    A ticket raised outside coverage (Sunday night, a holiday) starts its clock at the
    next covered minute — so the deadline lands inside working hours, never on a day
    nobody is rostered."""
    tz, days, w_start, w_end, holidays = cov["tz"], cov["days"], cov["start"], cov["end"], cov["holidays"]
    cursor = _aware(start).astimezone(tz)
    remaining = timedelta(minutes=int(minutes))

    for _ in range(_MAX_WALK_DAYS):
        day = cursor.date()
        covered_day = (day.isoweekday() in days) and (day.isoformat() not in holidays)
        if covered_day:
            win_open = datetime.combine(day, w_start, tzinfo=tz)
            win_close = datetime.combine(day, w_end, tzinfo=tz)
            if cursor < win_open:
                cursor = win_open
            if cursor < win_close:
                available = win_close - cursor
                if remaining <= available:
                    return (cursor + remaining).astimezone(timezone.utc)
                remaining -= available
        # jump to the next day's window start
        nxt = day + timedelta(days=1)
        cursor = datetime.combine(nxt, w_start, tzinfo=tz)

    # Misconfigured calendar (nothing covered for 2 years) — wall-clock fallback.
    return (_aware(start) + timedelta(minutes=int(minutes))).astimezone(timezone.utc)


def validate_coverage(cov) -> str | None:
    """Validate an inbound coverage payload; returns an error string or None when OK.
    Used by the SLA package create/update routes (422 on bad config)."""
    import re as _re
    if cov in (None, {}):
        return None
    if not isinstance(cov, dict):
        return "coverage must be an object"
    mode = cov.get("mode") or "24x7"
    if mode not in COVERAGE_MODES:
        return f"coverage.mode must be one of {sorted(COVERAGE_MODES)}"
    over = cov.get("priority_overrides")
    if over is not None:
        if not isinstance(over, dict) or any(v not in COVERAGE_MODES for v in over.values()):
            return "coverage.priority_overrides values must be '24x7' or 'business_hours'"
    if mode != "business_hours":
        return None
    days = cov.get("days") or []
    if not isinstance(days, list) or not days or any(not str(d).strip().isdigit() or not 1 <= int(d) <= 7 for d in days):
        return "coverage.days must be a non-empty list of ISO weekdays (1=Mon … 7=Sun)"
    hhmm = _re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")
    if not hhmm.match(str(cov.get("start") or "")) or not hhmm.match(str(cov.get("end") or "")):
        return "coverage.start / coverage.end must be HH:MM (24h)"
    st, en = _parse_hhmm(cov.get("start"), dtime(9, 0)), _parse_hhmm(cov.get("end"), dtime(18, 0))
    if (en.hour, en.minute) <= (st.hour, st.minute):
        return "coverage.end must be after coverage.start (overnight windows aren't supported)"
    try:
        ZoneInfo(str(cov.get("tz") or "UTC"))
    except Exception:
        return f"coverage.tz '{cov.get('tz')}' is not a valid IANA timezone"
    for h in (cov.get("holidays") or []):
        if not _re.match(r"^\d{4}-\d{2}-\d{2}$", str(h).strip()):
            return f"coverage.holidays entries must be YYYY-MM-DD (got '{h}')"
    return None


def compute_deadlines(package, priority: str, start: datetime | None = None):
    """Return (response_due_at, resolution_due_at) for a priority on a package.

    ``package`` is an SdSlaPackage (or None). Missing rows → (None, None) so the
    ticket simply has no SLA clock rather than erroring. When the package carries a
    business-hours coverage calendar the target minutes are counted in COVERED time
    (see add_covered_minutes); otherwise legacy wall-clock addition."""
    start = start or now_utc()
    matrix = getattr(package, "matrix", None) or {}
    row = matrix.get(priority) or matrix.get(str(priority)) or {}
    resp = row.get("response_mins")
    reso = row.get("resolution_mins")
    cov = resolve_coverage(package, priority)
    if cov is None:
        response_due = start + timedelta(minutes=int(resp)) if resp else None
        resolution_due = start + timedelta(minutes=int(reso)) if reso else None
        return response_due, resolution_due
    response_due = add_covered_minutes(start, int(resp), cov) if resp else None
    resolution_due = add_covered_minutes(start, int(reso), cov) if reso else None
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
