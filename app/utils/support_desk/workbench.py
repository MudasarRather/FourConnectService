"""Support Desk — agent-workbench aggregate (KPIs + smart insights).

`compute_workbench(db, base_query)` rolls the agent's (or employee's) ticket set
into the KPI cards + a 0-100 workload score + a list of heuristic "smart insights".
The insight generation is intentionally isolated in `_insights()` so it is a clean
seam: a later phase can swap the rule engine for an LLM (Claude) call returning the
same `WorkbenchInsight` shape without touching the routers.

All heuristics are DETERMINISTIC and run over the in-memory active set (a personal
queue is small), so the endpoint is cheap and side-effect free.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.support_desk.ticket import SdTicket
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES,
)
from app.schemas.support_desk.ticket import WorkbenchStats, WorkbenchInsight
from app.utils.support_desk import sla as sla_util


def _norm_subject(s: str | None) -> str:
    return " ".join((s or "").lower().split())[:60]


def compute_workbench(db: Session, base_query, actor=None) -> WorkbenchStats:
    """base_query = a SdTicket query already filtered to the relevant owner
    (assigned-to-me for an agent, raised/assigned for an employee) and is_deleted==False.
    `actor` (the calling User, when known) gates ACTION-suggesting insights: a merge
    nudge is only emitted for tickets the actor may actually command (owner-tier gate
    mirror) — never for tickets assigned to another agent."""
    now = sla_util.now_utc()
    out = WorkbenchStats()

    # Active set in memory (cap generously — a personal queue is small).
    active = base_query.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES | {TicketStatus.ON_HOLD.value})).limit(800).all()

    risk_ids, breached_ids, crit_ids, stale_ids, vendor_overdue_ids = [], [], [], [], []
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for t in active:
        st = t.status
        if st == TicketStatus.OPEN.value:
            out.open += 1
        elif st == TicketStatus.IN_PROGRESS.value:
            out.in_progress += 1
        elif st == TicketStatus.PENDING_CUSTOMER.value:
            out.pending_customer += 1
        elif st == TicketStatus.PENDING_VENDOR.value:
            out.pending_vendor += 1
            due = sla_util._aware(getattr(t, "vendor_due_at", None))
            if due and now > due:
                out.vendor_overdue += 1
                vendor_overdue_ids.append(t.id)
            disp = sla_util._aware(getattr(t, "vendor_dispatched_at", None))
            if disp and disp >= start_of_day:
                out.vendor_dispatched_today += 1
        elif st == TicketStatus.ON_HOLD.value:
            out.on_hold += 1
        if st == TicketStatus.ESCALATED.value or t.is_escalated:
            out.escalated += 1
        if t.priority == TicketPriority.CRITICAL.value:
            out.critical += 1
            crit_ids.append(t.id)

        rstate = sla_util.resolution_state(t, now)
        if t.sla_resolution_breached or rstate == "breached":
            out.sla_breached += 1
            breached_ids.append(t.id)
        elif rstate == "due-soon":
            out.sla_risk += 1
            risk_ids.append(t.id)

        # stale pending-customer (>3d since last customer reply or update)
        if st == TicketStatus.PENDING_CUSTOMER.value:
            ref = t.last_customer_reply_at or t.updated_at
            if ref and (now - sla_util._aware(ref)) > timedelta(days=3):
                stale_ids.append(t.id)

    out.pending_total = out.pending_customer + out.pending_vendor
    out.total_active = len([t for t in active if t.status in OPEN_TICKET_STATUSES])

    # resolved today (calendar day, server tz) + average resolution time.
    # Exclude merged duplicates: a merge force-CLOSES the duplicate (stamping resolved_at),
    # but that is folding-into-master, NOT a genuine resolution. The frontend hides these
    # tombstones (merged_into_id) from the list, so counting them here made "resolved today"
    # read higher than the visible "Resolved" count (e.g. 4 vs 2). Match the UI: skip them.
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    out.resolved_today = base_query.filter(
        SdTicket.merged_into_id.is_(None),
        SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= start_of_day).count()
    avg_secs = (base_query.filter(SdTicket.merged_into_id.is_(None), SdTicket.resolved_at.isnot(None))
                .with_entities(func.avg(func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at))).scalar())
    out.avg_resolution_minutes = round(float(avg_secs) / 60.0, 1) if avg_secs else None

    # Workload score (0-100, heuristic).
    out.workload_score = min(100, int(
        out.open * 4 + out.in_progress * 3 + out.pending_total * 2
        + out.critical * 10 + out.sla_breached * 12 + out.sla_risk * 6 + out.escalated * 5))

    out.insights = _insights(active, now, risk_ids, breached_ids, stale_ids, crit_ids, out.workload_score, actor=actor)
    # Vendor OLA overdue — a third-party hand-off has blown past its expected-return date.
    if vendor_overdue_ids:
        out.insights.insert(0, WorkbenchInsight(
            id="vendor_overdue", kind="pending_nudge", severity="warn",
            title=f"{len(vendor_overdue_ids)} vendor hand-off{'s' if len(vendor_overdue_ids) != 1 else ''} overdue",
            detail="Chase the vendor or bring the ticket back — the customer SLA is paused but the promise date passed.",
            action="view", ticket_ids=vendor_overdue_ids[:25]))
        out.insights = out.insights[:8]
    return out


def _insights(active, now, risk_ids, breached_ids, stale_ids, crit_ids, workload, actor=None):
    insights: list[WorkbenchInsight] = []

    # Owner-tier mirror for action insights: with a known non-superuser actor, an
    # actionable ticket is one that is unassigned or assigned to the actor. Without
    # an actor (legacy callers) every ticket counts — display-only surfaces.
    def _actionable(t):
        if actor is None or getattr(actor, "is_superuser", False):
            return True
        return t.assigned_agent_id is None or str(t.assigned_agent_id) == str(actor.id)

    # 1) Imminent SLA breach (next 30 minutes)
    soon = [t for t in active
            if t.resolution_due_at and not t.sla_resolution_breached
            and now < sla_util._aware(t.resolution_due_at) <= now + timedelta(minutes=30)]
    if soon:
        insights.append(WorkbenchInsight(
            id="breach_30", kind="breach_risk", severity="crit",
            title=f"{len(soon)} ticket{'s' if len(soon) != 1 else ''} likely to breach SLA in the next 30 min",
            detail="Resolve or escalate before the resolution clock expires.",
            action="resolve", ticket_ids=[t.id for t in soon][:25]))

    # 2) Already breached
    if breached_ids:
        insights.append(WorkbenchInsight(
            id="breached", kind="breach_risk", severity="crit",
            title=f"{len(breached_ids)} ticket{'s' if len(breached_ids) != 1 else ''} have breached SLA",
            detail="Triage the damage and capture a breach reason.",
            action="escalate", ticket_ids=breached_ids[:25]))

    # 3) Mergeable duplicates — same requester (email/user) with 2+ open tickets, similar subject
    by_requester: dict = defaultdict(list)
    for t in active:
        key = (t.contact_email or "").lower() or (str(t.raised_by_user_id) if t.raised_by_user_id else None)
        if key:
            by_requester[key].append(t)
    for key, group in by_requester.items():
        if len(group) >= 2:
            subs = Counter(_norm_subject(t.subject) for t in group)
            dup_sub, cnt = subs.most_common(1)[0]
            if cnt >= 2:
                # Merge is an owner-tier mutation — only nudge when the actor can command
                # EVERY ticket in the cluster (never suggest merging another agent's ticket).
                dups = [t for t in group if _norm_subject(t.subject) == dup_sub and _actionable(t)]
                if len(dups) < 2:
                    continue
                who = group[0].contact_name or group[0].contact_email or "the same requester"
                insights.append(WorkbenchInsight(
                    id=f"merge_{key}", kind="merge", severity="info",
                    title=f"{len(dups)} similar tickets from {who} can be merged",
                    detail="Same requester, near-identical subject — merge to avoid duplicate work.",
                    action="merge", ticket_ids=[t.id for t in dups][:10]))
                break  # one merge nudge is enough

    # 4) Customer flood — a requester with 4+ tickets created today
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_by_req: dict = defaultdict(list)
    for t in active:
        if t.created_at and sla_util._aware(t.created_at) >= start_of_day:
            key = (t.contact_email or "").lower() or (str(t.raised_by_user_id) if t.raised_by_user_id else None)
            if key:
                today_by_req[key].append(t)
    flood = max(today_by_req.values(), key=len, default=[])
    if len(flood) >= 4:
        who = flood[0].contact_name or flood[0].contact_email or "A requester"
        insights.append(WorkbenchInsight(
            id="flood", kind="customer_flood", severity="warn",
            title=f"{who} opened {len(flood)} tickets today",
            detail="Consider a single consolidated thread or a problem record.",
            action="view", ticket_ids=[t.id for t in flood][:25]))

    # 5) Stale pending-customer
    if stale_ids:
        insights.append(WorkbenchInsight(
            id="stale", kind="pending_nudge", severity="warn",
            title=f"{len(stale_ids)} ticket{'s' if len(stale_ids) != 1 else ''} stale awaiting the customer (3d+)",
            detail="Send a nudge or auto-resolve per policy.",
            action="reply", ticket_ids=stale_ids[:25]))

    # 6) Category concentration — a category dominating the active set
    cats = Counter(str(t.category_id) for t in active if t.category_id)
    if cats:
        top, cnt = cats.most_common(1)[0]
        if cnt >= 3 and cnt / max(1, len(active)) >= 0.4:
            insights.append(WorkbenchInsight(
                id="cat_spike", kind="category_spike", severity="info",
                title=f"{cnt} of your active tickets share one category",
                detail="A recurring theme — a problem record may resolve them at the root.",
                action="view", ticket_ids=[t.id for t in active if str(t.category_id) == top][:25]))

    # 7) Heavy workload — only when there's genuine VOLUME to rebalance. workload is
    #    risk-weighted (a few critical/breached tickets alone can push it past 75), so gate
    #    on the actual load too. The base query may be TEAM-scoped (command center), where
    #    the raw set size lies: 9 active tickets across 2 agents is a balanced desk, not a
    #    heavy one — and saying "100/100, rebalance" flatly contradicts the count-based
    #    Workload monitor sitting on the same page. So the gate follows that monitor's
    #    per-agent scale (balanced ≤4 · busy 5–8 · overloaded >8): fire only when the
    #    heaviest owner is genuinely overloaded. A personal queue has a single owner, so
    #    its heaviest load IS the set size and the old behavior is preserved.
    working = [t for t in active if t.status in OPEN_TICKET_STATUSES]
    owners = Counter(str(t.assigned_agent_id) for t in working if t.assigned_agent_id)
    heaviest = max(owners.values(), default=len(working))
    if workload >= 75 and len(working) >= 8 and heaviest > 8:
        detail = (
            f"{len(working)} active tickets · heaviest agent carries {heaviest} open (overload is >8). "
            "Ask a lead to reassign lower-priority items."
            if len(owners) > 1 else
            f"{len(working)} active tickets · workload score {workload}/100. Ask a lead to reassign lower-priority items."
        )
        insights.append(WorkbenchInsight(
            id="workload", kind="workload", severity="warn",
            title="Heavy workload — consider rebalancing",
            detail=detail, action="view", ticket_ids=[]))

    return insights[:8]
