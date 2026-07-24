"""Support Desk — RCA v2 single-truth helpers (Root Cause Analysis desks).

Same discipline as ``utils/support_desk/incidents.flag_condition``: the RCA board's
lens rows, the stats chips, the close gate and every legacy ``missing_rca`` predicate
ALL build from the conditions here, so a chip's count can never drift from the rows
its click returns — and the review machine has exactly one definition of "live".

The rca_status machine (column on ``support_tickets``):

    (NULL) ──file──► filed ──validate──► validated
       ▲               ▲ │                   │
       │               │ └────return──► returned ──re-file──► filed
       └─(never back)  │
        reopen: filed|validated|returned ──► stale ──re-file──► filed

``filed``/``validated`` are LIVE (they clear the debt); ``returned``/``stale`` read
as OWED. Legacy rows (rca_summary present, rca_status NULL — written before v2 or
by a not-yet-upgraded caller between deploys) READ as 'filed' via the effective-
status expression; the boot backfill also stamps them eagerly.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import and_, case, func, literal, null, or_

from app.models.support_desk.constants import (
    ResolutionCode, RootCauseCategory, TERMINAL_TICKET_STATUSES,
)
from app.models.support_desk.ticket import SdTicket
from app.utils.support_desk.incidents import breached_cond


# ─────────────────────────────── The machine ───────────────────────────────

RCA_STATUSES = ("filed", "validated", "returned", "stale")
# Statuses that CLEAR the RCA debt (an owed record stops being owed).
RCA_LIVE_STATUSES = ("filed", "validated")
# Resolution codes that make a terminal ticket EXEMPT from owing an RCA — a
# cancelled/withdrawn or silence-expired ticket was never "fixed", so there is no
# fix to explain. Mirrored by the close gate.
RCA_EXEMPT_RESOLUTION_CODES = (ResolutionCode.CANCELLED.value,
                               ResolutionCode.NO_RESPONSE.value)
RCA_CATEGORY_VALUES = {c.value for c in RootCauseCategory}
# Structured-capture caps (schema validators mirror these).
RCA_FIVE_WHYS_MAX = 5
RCA_FACTORS_MAX = 10
# Owed lens default window (days back from now over resolved/closed stamps).
RCA_OWED_WINDOW_DAYS = 30


def _summary_present_cond():
    """Non-empty rca_summary — the legacy 'an RCA exists' signal."""
    return and_(SdTicket.rca_summary.isnot(None),
                func.btrim(SdTicket.rca_summary) != "")


def rca_effective_status_expr():
    """SQL: the status every reader must use. Rows a pre-v2 writer minted (summary
    but NULL status) read as 'filed' so lenses stay truthful between deploys."""
    return case(
        (SdTicket.rca_status.isnot(None), SdTicket.rca_status),
        (_summary_present_cond(), literal("filed")),
        else_=null(),
    )


def rca_effective_status(t: SdTicket) -> str | None:
    """Python twin of rca_effective_status_expr — same truth for row-level gates."""
    if t.rca_status:
        return t.rca_status
    if (t.rca_summary or "").strip():
        return "filed"
    return None


def rca_live_cond():
    """Effective status ∈ live — the RCA debt is cleared."""
    return rca_effective_status_expr().in_(RCA_LIVE_STATUSES)


def rca_absent_cond():
    """NOT live: no RCA at all, or a returned/stale one — the ticket still OWES.

    Spelled as an explicit OR, never ``~rca_live_cond()`` — for a NULL effective
    status, SQL's ``NOT (NULL IN (...))`` is NULL (three-valued logic) and the
    no-RCA rows would silently vanish from every owed/missing lens."""
    eff = rca_effective_status_expr()
    return or_(eff.is_(None), eff.in_(("returned", "stale")))


def rca_required_cond():
    """Which tickets owe an RCA at all: anything that breached its SLA, any major
    incident, and any priority-critical record (SEV1 ∪ SEV2 ∪ breached)."""
    return or_(breached_cond(),
               SdTicket.is_major_incident == True,  # noqa: E712
               SdTicket.priority == "critical")


def rca_required(t: SdTicket) -> bool:
    """Python twin of rca_required_cond."""
    return bool(t.sla_response_breached or t.sla_resolution_breached
                or t.is_major_incident or (t.priority or "") == "critical")


def rca_exempt_cond():
    """Terminal outcomes that never owe an RCA (cancelled / silence-expired)."""
    return SdTicket.resolution_code.in_(RCA_EXEMPT_RESOLUTION_CODES)


def _terminal_window_cond(now, days: int):
    cutoff = now - timedelta(days=days)
    return and_(SdTicket.status.in_(list(TERMINAL_TICKET_STATUSES)),
                func.coalesce(SdTicket.resolved_at, SdTicket.closed_at,
                              SdTicket.created_at) >= cutoff)


def rca_eligible_cond(now, days: int = RCA_OWED_WINDOW_DAYS):
    """The coverage denominator: terminal-in-window tickets that OWE an RCA
    (required, not exempt, not merge tombstones, not deleted)."""
    return and_(SdTicket.is_deleted == False,  # noqa: E712
                SdTicket.merged_into_id.is_(None),
                _terminal_window_cond(now, days),
                rca_required_cond(),
                ~rca_exempt_cond())


def rca_owed_cond(now, days: int = RCA_OWED_WINDOW_DAYS):
    """Eligible AND still uncovered — the debt lens."""
    return and_(rca_eligible_cond(now, days), rca_absent_cond())


RCA_LENSES = ("owed", "pending", "returned", "validated", "stale", "all")


def rca_lens_condition(lens: str, now, days: int = RCA_OWED_WINDOW_DAYS):
    """Condition for one RCA-board lens. Compose WITH the team seal upstream.
    422-validate ``lens`` against RCA_LENSES before calling."""
    base = and_(SdTicket.is_deleted == False,  # noqa: E712
                SdTicket.merged_into_id.is_(None))
    eff = rca_effective_status_expr()
    lens = (lens or "owed").lower()
    if lens == "owed":
        return rca_owed_cond(now, days)
    if lens == "pending":     # filed, awaiting a lead/admin ruling
        return and_(base, eff == "filed")
    if lens == "returned":
        return and_(base, eff == "returned")
    if lens == "validated":
        return and_(base, eff == "validated")
    if lens == "stale":       # reopen invalidated the filed story
        return and_(base, eff == "stale")
    # all — anything with an RCA story either way: owed debt ∪ any effective status
    return and_(base, or_(rca_owed_cond(now, days), eff.isnot(None)))


def rca_missing_legacy_cond():
    """Drop-in for the four legacy ``missing_rca`` predicates (breached lists, tier
    boards, incident stats): no coded breach_reason AND no live RCA. v2 truth-fix:
    returned/stale rows now correctly count as missing."""
    return and_(or_(SdTicket.breach_reason.is_(None), SdTicket.breach_reason == ""),
                rca_absent_cond())


# ─────────────────────────────── The close gate ───────────────────────────────

def rca_close_gate_applies(t: SdTicket, resolution_code: str | None = None) -> bool:
    """Does closing THIS record demand a live RCA first? Breached ∪ MI ∪ priority
    critical, unless the outcome is exempt (cancelled / no_response) or the record
    is a merge tombstone (RCA lives on the surviving master)."""
    code = (resolution_code or t.resolution_code or "").lower()
    if code in RCA_EXEMPT_RESOLUTION_CODES:
        return False
    if t.merged_into_id is not None:
        return False
    return rca_required(t)


def rca_close_block_reason(t: SdTicket, resolution_code: str | None = None) -> str | None:
    """The gate as a REASON (None = clear to close). Bulk close folds this into
    per-item skip_reason; single-close raises it as a 422."""
    if not rca_close_gate_applies(t, resolution_code):
        return None
    if rca_effective_status(t) in RCA_LIVE_STATUSES:
        return None
    kind = ("major incident" if t.is_major_incident
            else "critical record" if (t.priority or "") == "critical"
            else "SLA-breached record")
    return (f"{t.ticket_number} is a {kind} with no root-cause analysis on file — "
            f"file its RCA first (the record can stay resolved meanwhile).")


def require_rca_before_close(t: SdTicket, resolution_code: str | None = None) -> None:
    """422 drop-gate for the MANUAL close paths (single resolve-with-close, agent
    self-close). System sweeps (lazy/cron auto-close) are deliberately exempt —
    swept debt stays visible in the owed lens, keyed off terminal status."""
    reason = rca_close_block_reason(t, resolution_code)
    if reason:
        raise HTTPException(422, reason)
