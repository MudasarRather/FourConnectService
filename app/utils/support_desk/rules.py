"""Support Desk — the routing-rule engine (Queue Engine phase).

Executes ``SdAutomationRule`` rows (the condition→action store that shipped in
Phase 3 with the docstring "Phase 6 wires the engine" — this is that engine).

Two triggers:
  • ``on_create``  — run by every ticket-create path (admin, self, public portal)
    right after the ticket row is flushed, BEFORE ``route_and_assign``. Rules only
    ROUTE/CLASSIFY (stamp queue/team/priority/SLA/tags/assignee); auto-assignment
    stays the single engine in ``assignment.route_and_assign``, which honours an
    explicit ``queue_id`` and picks the agent.
  • ``time_based`` — swept against aged open tickets; doubles as the configurable
    auto-escalation policy store ("unresolved X mins at priority Y → escalate to
    tier Z"). Idempotent per (ticket, rule) via a ``rule_fired`` activity stamp.

First-match semantics: rules run in ``order_index`` order; a matching rule with
``stop_processing`` ends the chain (Zendesk queue evaluation / ServiceNow first-
queue-wins). Dead targets (deleted/inactive queue or team) are skipped with an
activity note so a stale rule can never route work into a void.

Never raises — a rule failure must never block ticket creation. Mutates in the
caller's session; the caller commits.
"""
from __future__ import annotations

from datetime import timedelta

from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.ops import SdAutomationRule
from app.models.support_desk.workspace import SdTeam, SdQueue
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, PRIORITY_ORDER, SLA_PAUSE_STATUSES,
    TERMINAL_TICKET_STATUSES, EscalationType,
)
from app.utils.support_desk import sla as sla_util

# Fields a rule condition may inspect (safe whitelist — anything else is ignored).
# ``business_hours`` is VIRTUAL — not a ticket column; evaluated against the desk
# schedule (routed team's hours, else the default lane's) + the desk holiday list.
_COND_FIELDS = {
    "ticket_type", "priority", "source", "status", "impact", "urgency",
    "category_id", "subcategory_id", "organization_id", "queue_id", "team_id",
    "subject", "description", "tags", "is_major_incident", "contact_email",
    "department", "location", "business_hours",
}

_VALID_PRIORITIES = {p.value for p in TicketPriority}

# Ordered scales for gte/lte ranking conditions (impact/urgency share one ladder).
_SEVERITY_ORDER = ["low", "medium", "high", "critical"]
_RANKED_FIELDS = {"priority": PRIORITY_ORDER, "impact": _SEVERITY_ORDER, "urgency": _SEVERITY_ORDER}


def _business_hours_state(db, t, now) -> str:
    """'in_hours' | 'out_of_hours' for the ticket's effective schedule.

    Schedule resolution: the ticket's team's ``business_hours`` → the default lane's
    own hours → the default lane's team's hours. A desk holiday (settings key
    ``business_holidays``, ``{items:[{date,label}]}``) is always out-of-hours.
    No schedule anywhere ⇒ a 24/7 desk ⇒ always in_hours (the condition can then
    only match via ``eq out_of_hours`` on holidays)."""
    try:
        from app.models.support_desk.ops import SdSetting
        bh, team = None, None
        if t.team_id:
            team = db.query(SdTeam).filter(SdTeam.id == t.team_id).first()
        if team is None:
            dq = (db.query(SdQueue)
                  .filter(SdQueue.is_deleted == False, SdQueue.is_active == True,  # noqa: E712
                          SdQueue.is_default == True).first())  # noqa: E712
            if dq is not None:
                bh = dq.business_hours or None
                if not bh and dq.team_id:
                    team = db.query(SdTeam).filter(SdTeam.id == dq.team_id).first()
        if not bh and team is not None:
            bh = team.business_hours or None

        # Holiday gate — today's date in the schedule's tz (defensively UTC).
        s = db.query(SdSetting).filter(SdSetting.key == "business_holidays").first()
        items = ((s.value or {}).get("items") or []) if s else []
        if items:
            local = now
            try:
                from zoneinfo import ZoneInfo
                local = now.astimezone(ZoneInfo(str((bh or {}).get("tz") or "Asia/Kolkata")))
            except Exception:
                pass
            today = local.strftime("%Y-%m-%d")
            if any(str((i or {}).get("date")) == today for i in items if isinstance(i, dict)):
                return "out_of_hours"

        from app.utils.support_desk.team_ops import team_on_shift
        on = team_on_shift(bh, now)
        return "out_of_hours" if on is False else "in_hours"
    except Exception:
        return "in_hours"


def _field_value(t, field: str):
    v = getattr(t, field, None)
    if field == "tags":
        return [str(x).lower() for x in (v or [])]
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).lower()


def _cond_matches(t, cond: dict, ctx: dict | None = None) -> bool:
    """One condition row: {field, op, value}. Unknown fields/ops never match.
    ``ctx`` carries {db, now} for virtual fields (business_hours), memoized per
    evaluation so one rule chain costs at most one schedule lookup."""
    field = str(cond.get("field") or "")
    if field not in _COND_FIELDS:
        return False
    op = str(cond.get("op") or "eq")
    raw = cond.get("value")

    if field == "business_hours":
        # Virtual field — value is 'in_hours' | 'out_of_hours'.
        if ctx is None or ctx.get("db") is None:
            return False
        state = ctx.get("_bh_state")
        if state is None:
            state = _business_hours_state(ctx["db"], t, ctx.get("now") or sla_util.now_utc())
            ctx["_bh_state"] = state
        want = str(raw or "").strip().lower()
        if op == "eq":
            return state == want
        if op == "neq":
            return state != want
        return False

    actual = _field_value(t, field)

    def norm(x):
        return str(x).lower() if x is not None else None

    if op == "is_empty":
        return actual in (None, "", []) or actual == []
    if op == "not_empty":
        return not (actual in (None, "", []) or actual == [])

    if isinstance(actual, list):  # tags
        vals = [norm(v) for v in raw] if isinstance(raw, list) else [norm(raw)]
        vals = [v for v in vals if v is not None]
        if op in ("contains", "eq", "in"):
            return any(v in actual for v in vals)
        if op in ("not_contains", "neq", "not_in"):
            return not any(v in actual for v in vals)
        return False

    if isinstance(actual, bool):
        want = raw if isinstance(raw, bool) else norm(raw) in ("true", "1", "yes")
        if op == "eq":
            return actual == want
        if op == "neq":
            return actual != want
        return False

    value = norm(raw)
    if op == "eq":
        return actual == value
    if op == "neq":
        return actual != value
    if op == "in":
        vals = [norm(v) for v in raw] if isinstance(raw, list) else [value]
        return actual in vals
    if op == "not_in":
        vals = [norm(v) for v in raw] if isinstance(raw, list) else [value]
        return actual not in vals
    if op == "contains":
        return bool(value) and bool(actual) and value in actual
    if op == "not_contains":
        return not (bool(value) and bool(actual) and value in actual)
    if op == "matches_keywords":
        # Any-of keyword screen (comma list or array), case-insensitive substring.
        if not isinstance(actual, str) or not actual:
            return False
        kws = raw if isinstance(raw, list) else str(raw or "").split(",")
        kws = [str(k).strip().lower() for k in kws if str(k).strip()]
        return any(k in actual for k in kws)
    if op in ("gte", "lte") and field in _RANKED_FIELDS:
        ladder = _RANKED_FIELDS[field]
        try:
            a, b = ladder.index(actual or ""), ladder.index(value or "")
        except ValueError:
            return False
        return a >= b if op == "gte" else a <= b
    return False


def _rule_matches(t, rule: SdAutomationRule, ctx: dict | None = None) -> bool:
    conds = [c for c in (rule.conditions or []) if isinstance(c, dict)]
    if not conds:
        return False   # a condition-less rule matches nothing (never a catch-all by accident)
    hits = (_cond_matches(t, c, ctx) for c in conds)
    return all(hits) if (rule.match_type or "all") == "all" else any(hits)


def _as_uuid(value):
    """Parse-or-None — validated in Python BEFORE it reaches SQL, so a name-shaped
    value can never abort the surrounding Postgres transaction with a cast error."""
    import uuid as _uuid
    try:
        return _uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _load_queue(db, value) -> SdQueue | None:
    """Resolve a queue action target by UUID or (case-insensitive) name/code."""
    if not value:
        return None
    base = db.query(SdQueue).filter(SdQueue.is_deleted == False, SdQueue.is_active == True)  # noqa: E712
    uid = _as_uuid(value)
    if uid is not None:
        return base.filter(SdQueue.id == uid).first()
    v = str(value).strip().lower()
    for q in base.all():
        if (q.name or "").lower() == v or (q.code or "").lower() == v:
            return q
    return None


def _load_team(db, value) -> SdTeam | None:
    if not value:
        return None
    base = db.query(SdTeam).filter(SdTeam.is_deleted == False, SdTeam.is_active == True)  # noqa: E712
    uid = _as_uuid(value)
    if uid is not None:
        return base.filter(SdTeam.id == uid).first()
    v = str(value).strip().lower()
    for tm in base.all():
        if (tm.name or "").lower() == v or (tm.code or "").lower() == v:
            return tm
    return None


def find_tier_queue(db, tier: int, category_id=None, exclude_id=None, subcategory_id=None) -> SdQueue | None:
    """The best target queue at a tier: subcategory match first (most specific), then
    category match, then the tier's highest-``queue_priority`` queue. Used by tier
    escalation (manual + time-based)."""
    queues = (db.query(SdQueue)
              .filter(SdQueue.is_deleted == False, SdQueue.is_active == True,  # noqa: E712
                      SdQueue.tier == int(tier))
              .order_by(SdQueue.queue_priority.desc(), SdQueue.name).all())
    queues = [q for q in queues if not (exclude_id and str(q.id) == str(exclude_id))]
    if not queues:
        return None
    for wanted in (subcategory_id, category_id):
        if not wanted:
            continue
        w = str(wanted)
        for q in queues:
            if w in [str(x) for x in (q.category_ids or [])]:
                return q
    return queues[0]


def apply_tier_move(db, t: SdTicket, queue: SdQueue, *, actor_id=None,
                    actor_name: str = "System", direction: str = "escalate",
                    detail: dict | None = None) -> None:
    """Re-park a ticket on another tier's queue (queue_id + team_id restamp) and log it.
    The escalation record itself (level/type/reason/ack clock) is the caller's job via
    ``apply_escalation`` / the de-escalate writer — this only moves the lane."""
    prev_q = str(t.queue_id) if t.queue_id else None
    t.queue_id = queue.id
    if queue.team_id:
        t.team_id = queue.team_id
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=actor_id, actor_name=actor_name,
        action="tier_moved",
        detail={**(detail or {}), "direction": direction, "queue": queue.name,
                "queue_id": str(queue.id), "tier": queue.tier, "from_queue_id": prev_q}))


def _apply_actions(db, t: SdTicket, rule: SdAutomationRule, *,
                   dry_run: bool, decision: dict, now=None) -> None:
    """Apply (or, dry_run, record) one matched rule's actions onto the ticket."""
    now = now or sla_util.now_utc()
    for act in (rule.actions or []):
        if not isinstance(act, dict):
            continue
        kind = str(act.get("type") or "")
        value = act.get("value")

        if kind in ("route_queue", "assign_queue"):
            queue = _load_queue(db, value)
            if not queue:
                decision.setdefault("skipped", []).append({"rule": rule.name, "action": kind, "reason": "dead_target"})
                continue
            decision.update({"queue_id": str(queue.id), "queue_name": queue.name, "via": f"rule:{rule.name}"})
            if queue.team_id:
                decision.update({"team_id": str(queue.team_id)})
            if not dry_run:
                t.queue_id = queue.id
                if queue.team_id:
                    t.team_id = queue.team_id

        elif kind in ("route_team", "assign_team"):
            team = _load_team(db, value)
            if not team:
                decision.setdefault("skipped", []).append({"rule": rule.name, "action": kind, "reason": "dead_target"})
                continue
            decision.update({"team_id": str(team.id), "team_name": team.name, "via": f"rule:{rule.name}"})
            if not dry_run:
                t.team_id = team.id

        elif kind == "set_priority":
            v = str(value or "").lower()
            if v in _VALID_PRIORITIES:
                decision["priority"] = v
                if not dry_run and t.priority != v:
                    t.priority = v
                    decision["_recompute_sla"] = True

        elif kind == "set_sla_package":
            pkg_id = _as_uuid(value)
            decision["sla_package_id"] = str(pkg_id) if pkg_id else None
            if not dry_run and pkg_id:
                t.sla_package_id = pkg_id
                decision["_recompute_sla"] = True

        elif kind == "add_tags":
            tags = value if isinstance(value, list) else [value]
            tags = [str(x) for x in tags if x]
            if tags:
                decision["tags"] = sorted(set((decision.get("tags") or []) + tags))
                if not dry_run:
                    existing = [str(x) for x in (t.tags or [])]
                    merged = existing + [x for x in tags if x not in existing]
                    t.tags = merged

        elif kind == "set_assignee":
            agent_id = _as_uuid(value)
            decision["assigned_agent_id"] = str(agent_id) if agent_id else None
            if not dry_run and agent_id and not t.assigned_agent_id:
                t.assigned_agent_id = agent_id

        elif kind == "escalate_tier":
            # Only meaningful on the time_based sweep (a brand-new ticket has no tier
            # history to escalate from); recorded for the simulator either way.
            decision["escalate_tier"] = value
            if not dry_run and rule.trigger == "time_based":
                _sweep_escalate_tier(db, t, value, rule, now)

        elif kind in ("notify_team_lead", "notify_assignee"):
            decision.setdefault("notify", []).append(kind)
            if not dry_run:
                _sweep_notify(db, t, kind)


def _sweep_escalate_tier(db, t: SdTicket, value, rule: SdAutomationRule, now) -> None:
    """time_based escalate action: lift the ticket into the target tier's queue and
    write the standard escalation record (idempotent — the caller pre-filters tickets
    already at/above the target tier)."""
    from app.utils.support_desk.escalation import apply_escalation
    try:
        tier = int(value)
    except (TypeError, ValueError):
        return
    if tier not in (1, 2, 3):
        return
    queue = find_tier_queue(db, tier, t.category_id, exclude_id=t.queue_id, subcategory_id=t.subcategory_id)
    if not queue:
        return
    apply_escalation(
        db, t, None, "Automation",
        reason=f"Rule '{rule.name}': open past {rule.time_threshold_mins or 0} minutes",
        reason_code="sla_risk", escalation_type=EscalationType.FUNCTIONAL.value,
        to_team_id=queue.team_id, auto=True, now=now)
    apply_tier_move(db, t, queue, actor_name="Automation", direction="escalate",
                    detail={"rule_id": str(rule.id), "rule": rule.name})


def _sweep_notify(db, t: SdTicket, kind: str) -> None:
    from app.routers.support_desk._common import _notify_safe
    from app.models.support_desk.constants import EVT_TICKET_STATUS
    target = None
    if kind == "notify_assignee":
        target = t.assigned_agent_id
    elif kind == "notify_team_lead" and t.team_id:
        team = db.query(SdTeam).filter(SdTeam.id == t.team_id).first()
        target = team.lead_user_id if team else None
    if target:
        _notify_safe(db, EVT_TICKET_STATUS, target, t,
                     title=f"Automation rule flagged {t.ticket_number}",
                     action_url="/user/support/queues/overview")


def evaluate_rules(db, t: SdTicket, *, trigger: str = "on_create",
                   dry_run: bool = False, now=None) -> dict:
    """Run the rule chain against one ticket. Returns
    ``{"matched": [{rule_id,name,order_index,actions,stopped}], "decision": {...}}``.

    ``dry_run`` records the decision without mutating the ticket or stamping rule
    counters (powers ``POST /automation-rules/simulate``).
    """
    out = {"matched": [], "decision": {}}
    try:
        now = now or sla_util.now_utc()
        ctx = {"db": db, "now": now}   # virtual-field context (business_hours memo)
        rules = (db.query(SdAutomationRule)
                 .filter(SdAutomationRule.is_deleted == False,  # noqa: E712
                         SdAutomationRule.is_active == True,  # noqa: E712
                         SdAutomationRule.trigger == trigger)
                 .order_by(SdAutomationRule.order_index, SdAutomationRule.created_at)
                 .all())
        for rule in rules:
            if not _rule_matches(t, rule, ctx):
                continue
            _apply_actions(db, t, rule, dry_run=dry_run, decision=out["decision"], now=now)
            stopped = bool(rule.stop_processing)
            out["matched"].append({
                "rule_id": str(rule.id), "name": rule.name,
                "order_index": rule.order_index,
                "actions": rule.actions or [], "stopped": stopped,
            })
            if not dry_run:
                rule.last_run_at = now
                rule.run_count = (rule.run_count or 0) + 1
                db.add(SdTicketActivity(
                    ticket_id=t.id, actor_user_id=None, actor_name="Automation",
                    action="rule_fired",
                    detail={"rule_id": str(rule.id), "rule": rule.name,
                            "trigger": trigger,
                            "actions": [a.get("type") for a in (rule.actions or []) if isinstance(a, dict)]}))
            if stopped:
                break
        # Rules changed priority / SLA package → the deadlines stamped before the rules
        # ran are stale; recompute from creation so the clock matches the new class.
        if not dry_run and out["decision"].pop("_recompute_sla", None):
            from app.routers.support_desk._common import resolve_sla_package
            pkg = resolve_sla_package(db, t.sla_package_id, t.organization_id)
            if pkg is not None:
                t.sla_package_id = pkg.id
            rd, rsd = sla_util.compute_deadlines(pkg, t.priority, start=t.created_at or now)
            if t.first_responded_at is None:
                t.response_due_at = rd
            if t.resolved_at is None:
                t.resolution_due_at = rsd
        else:
            out["decision"].pop("_recompute_sla", None)
    except Exception:
        # The engine must never block ticket creation.
        pass
    return out


def apply_default_queue(db, t: SdTicket) -> None:
    """Zendesk standard-queue fallback: a ticket that neither the rules nor the
    category/type router placed anywhere lands in the ``is_default`` queue (if one is
    configured) so nothing is ever unroutable. Called after ``route_and_assign``.
    Honours the capacity gate (a full default lane spills to its overflow target)
    and the lane's per-queue SLA policy — same semantics as every other route path."""
    try:
        if t.queue_id or t.team_id:
            return
        q = (db.query(SdQueue)
             .filter(SdQueue.is_deleted == False, SdQueue.is_active == True,  # noqa: E712
                     SdQueue.is_default == True)  # noqa: E712
             .first())
        if not q:
            return
        from app.utils.support_desk.assignment import apply_overflow, apply_queue_sla
        q, hopped = apply_overflow(db, q)
        t.queue_id = q.id
        if q.team_id:
            t.team_id = q.team_id
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="Routing",
            action="routed",
            detail={"queue": q.name, "by": "overflow" if hopped else "default_queue"}))
        apply_queue_sla(db, t, q)
    except Exception:
        pass


def sweep_time_based_rules(db, *, cap: int = 300) -> int:
    """Evaluate ``time_based`` rules against aged, actively-worked tickets.

    Candidates per rule: not deleted, non-terminal, NOT SLA-paused (escalating a
    parked ticket would bypass hold/pending bookkeeping), older than the rule's
    ``time_threshold_mins``, matching the rule's conditions, and not already fired
    by this rule (``rule_fired`` activity stamp = the idempotency key). For
    ``escalate_tier`` actions, tickets already at/above the target tier are skipped.
    The caller commits. Returns how many tickets any rule fired on.
    """
    fired = 0
    try:
        now = sla_util.now_utc()
        rules = (db.query(SdAutomationRule)
                 .filter(SdAutomationRule.is_deleted == False,  # noqa: E712
                         SdAutomationRule.is_active == True,  # noqa: E712
                         SdAutomationRule.trigger == "time_based")
                 .order_by(SdAutomationRule.order_index, SdAutomationRule.created_at)
                 .all())
        if not rules:
            return 0
        active_statuses = [s for s in
                           (set(TicketStatus._value2member_map_.keys()) - TERMINAL_TICKET_STATUSES - SLA_PAUSE_STATUSES)]
        for rule in rules:
            threshold = int(rule.time_threshold_mins or 0)
            cutoff = now - timedelta(minutes=threshold)
            candidates = (db.query(SdTicket)
                          .filter(SdTicket.is_deleted == False,  # noqa: E712
                                  SdTicket.status.in_(active_statuses),
                                  SdTicket.merged_into_id.is_(None),
                                  SdTicket.created_at <= cutoff)
                          .order_by(SdTicket.created_at)
                          .limit(cap).all())
            if not candidates:
                continue
            # Idempotency: which of these already had THIS rule fire?
            seen = {str(r[0]) for r in (
                db.query(SdTicketActivity.ticket_id)
                .filter(SdTicketActivity.ticket_id.in_([t.id for t in candidates]),
                        SdTicketActivity.action == "rule_fired",
                        SdTicketActivity.detail["rule_id"].astext == str(rule.id))
                .all())}
            # escalate_tier targets — skip tickets already at/above the tier.
            esc_tiers = [a.get("value") for a in (rule.actions or [])
                         if isinstance(a, dict) and a.get("type") == "escalate_tier"]
            target_tier = None
            if esc_tiers:
                try:
                    target_tier = int(esc_tiers[0])
                except (TypeError, ValueError):
                    target_tier = None
            queue_tiers = {}
            if target_tier:
                queue_tiers = {str(q.id): q.tier for q in
                               db.query(SdQueue).filter(SdQueue.tier.isnot(None)).all()}
            for t in candidates:
                if str(t.id) in seen:
                    continue
                if target_tier:
                    cur_tier = queue_tiers.get(str(t.queue_id)) if t.queue_id else None
                    if cur_tier is not None and cur_tier >= target_tier:
                        continue
                if not _rule_matches(t, rule, {"db": db, "now": now}):
                    continue
                decision = {}
                _apply_actions(db, t, rule, dry_run=False, decision=decision, now=now)
                rule.last_run_at = now
                rule.run_count = (rule.run_count or 0) + 1
                db.add(SdTicketActivity(
                    ticket_id=t.id, actor_user_id=None, actor_name="Automation",
                    action="rule_fired",
                    detail={"rule_id": str(rule.id), "rule": rule.name, "trigger": "time_based",
                            "actions": [a.get("type") for a in (rule.actions or []) if isinstance(a, dict)]}))
                fired += 1
    except Exception:
        pass
    return fired
