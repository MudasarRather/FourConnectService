"""Support Desk — ticket routing + auto-assignment engine (Phase 1-2).

Two stages, both invoked once on ticket creation:

  1. ROUTE  — find the queue whose `category_ids` includes the ticket's category,
              stamp `queue_id` + `team_id`. (Skipped if the ticket already names a
              queue, e.g. an agent picked one.)
  2. ASSIGN — if that queue has `auto_assign` on and a method other than 'manual',
              pick an agent from the queue's team and set `assigned_agent_id`:
                • round_robin   — rotate by a per-queue cursor (`rr_last_user_id`)
                • load_balanced — the active member with the fewest open tickets

Corporate model (ServiceNow/Zendesk/JSM): the requester picks *what* + *how urgent*;
the system routes to a group and either auto-claims (round-robin / load-balanced) or
leaves it UNASSIGNED in the queue for manual triage. Never raises — a routing failure
must never block ticket creation. Mutates the ticket/queue in the caller's session;
the caller commits + dispatches notifications.
"""
from __future__ import annotations

from sqlalchemy import func

from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.workspace import SdTeam, SdQueue, SdSkill, SdAgentStatus
from app.models.support_desk.constants import TERMINAL_TICKET_STATUSES

# Agent statuses that make a member INELIGIBLE for auto-assignment (Zendesk unified
# agent status). 'focus' still receives work — it only mutes desk chatter; away/offline
# do not. An agent with NO status row counts as online.
UNAVAILABLE_STATUSES = {"away", "offline"}


def _open_in_queue(db, queue_id) -> int:
    """ACTIVE work in a queue (non-terminal, merged tombstones excluded) — the same
    definition the delete/deactivate guard uses, so capacity means one thing."""
    return (db.query(SdTicket)
            .filter(SdTicket.queue_id == queue_id, SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None),
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
            .count())


def apply_overflow(db, queue: SdQueue):
    """Capacity gate (config v2): a queue at/over its ``capacity_limit`` spills new
    work to its ``overflow_queue_id`` — ONE hop only (never chained), FAIL-OPEN
    (no/invalid/inactive overflow target → the ticket stays put; a full queue is
    better than a lost one). Returns ``(final_queue, hopped)``. Creation-time
    routing only — manual moves never pass through this gate."""
    try:
        cap = getattr(queue, "capacity_limit", None)
        of_id = getattr(queue, "overflow_queue_id", None)
        if not cap or int(cap) <= 0 or not of_id:
            return queue, False
        if _open_in_queue(db, queue.id) < int(cap):
            return queue, False
        target = (db.query(SdQueue)
                  .filter(SdQueue.id == of_id, SdQueue.is_deleted == False,  # noqa: E712
                          SdQueue.is_active == True).first())  # noqa: E712
        if not target or str(target.id) == str(queue.id):
            return queue, False
        return target, True
    except Exception:
        return queue, False


def apply_queue_sla(db, t: SdTicket, queue: SdQueue) -> None:
    """Per-queue SLA policy (config v2): a lane with its own ``sla_package_id``
    re-classes tickets that landed carrying only the desk-default package (or none).
    Precedence stays explicit/rule > organization > QUEUE > default — an org contract
    or a rule's ``set_sla_package`` always wins. Recomputes the still-open deadlines
    from creation so the clock matches the new class. Best-effort, never raises."""
    try:
        pkg_id = getattr(queue, "sla_package_id", None)
        if not pkg_id or str(t.sla_package_id or "") == str(pkg_id):
            return
        from app.models.support_desk.core import SdSlaPackage, SdOrganization
        if t.sla_package_id is not None:
            # Org-derived package wins over the queue's.
            if t.organization_id:
                org = db.query(SdOrganization).filter(SdOrganization.id == t.organization_id).first()
                if org and org.sla_package_id and str(org.sla_package_id) == str(t.sla_package_id):
                    return
            # Anything other than the desk default was explicitly chosen (agent pick
            # or a rule's set_sla_package) — leave it alone.
            default = (db.query(SdSlaPackage)
                       .filter(SdSlaPackage.is_default == True, SdSlaPackage.is_deleted == False)  # noqa: E712
                       .first())
            if not default or str(default.id) != str(t.sla_package_id):
                return
        pkg = (db.query(SdSlaPackage)
               .filter(SdSlaPackage.id == pkg_id, SdSlaPackage.is_deleted == False)  # noqa: E712
               .first())
        if not pkg:
            return
        from app.utils.support_desk import sla as sla_util
        t.sla_package_id = pkg.id
        rd, rsd = sla_util.compute_deadlines(pkg, t.priority, start=t.created_at or sla_util.now_utc())
        if getattr(t, "first_responded_at", None) is None:
            t.response_due_at = rd
        if getattr(t, "resolved_at", None) is None:
            t.resolution_due_at = rsd
        db.add(SdTicketActivity(
            ticket_id=t.id, actor_user_id=None, actor_name="Routing",
            action="sla_reclassed",
            detail={"queue": queue.name, "package": pkg.name, "by": "queue_sla"}))
    except Exception:
        pass


def _filter_available(db, pool: list) -> list:
    """Drop away/offline agents from an auto-assign pool. FAIL-OPEN: if that empties
    the pool (whole team signed off), the original pool is returned — a queue must
    never wedge because presence data says nobody is home."""
    if not pool:
        return pool
    rows = (db.query(SdAgentStatus)
            .filter(SdAgentStatus.user_id.in_([u.id for u in pool]),
                    SdAgentStatus.status.in_(list(UNAVAILABLE_STATUSES))).all())
    unavailable = {str(r.user_id) for r in rows}
    if not unavailable:
        return pool
    kept = [u for u in pool if str(u.id) not in unavailable]
    return kept or pool


def _filter_skilled(db, queue: SdQueue, pool: list) -> list:
    """Keep agents holding ALL of the queue's required skills (Zendesk required-skills
    routing). FAIL-OPEN: no qualified agent → the whole pool, so skill gaps degrade to
    ordinary team routing instead of starving the queue."""
    skill_ids = [str(x) for x in (getattr(queue, "skill_ids", None) or [])]
    if not pool or not skill_ids:
        return pool
    skills = (db.query(SdSkill)
              .filter(SdSkill.id.in_(skill_ids), SdSkill.is_deleted == False,  # noqa: E712
                      SdSkill.is_active == True).all())  # noqa: E712
    if not skills:
        return pool   # every referenced skill was deleted/retired — nothing to gate on
    rosters = [{str(a) for a in (s.agent_ids or [])} for s in skills]
    kept = [u for u in pool if all(str(u.id) in r for r in rosters)]
    return kept or pool


def _find_queue_for_category(db, category_id, subcategory_id=None) -> SdQueue | None:
    """The best active queue that routes this category OR subcategory (Python filter —
    queue counts are tiny and JSONB element-containment operators are finicky across
    drivers). A lane whose list names the ticket's SUBCATEGORY wins over one naming
    only the parent category (most-specific-first — a "Laptop / Desktop" lane must
    catch a Hardware>Laptop ticket even though the ticket's category_id is Hardware).
    Within the same specificity, drain priority decides — NOT the accident of
    alphabetical order (an "L1" lane must not shadow "L2" just by name)."""
    if not category_id and not subcategory_id:
        return None
    queues = db.query(SdQueue).filter(
        SdQueue.is_active == True, SdQueue.is_deleted == False,  # noqa: E712
    ).order_by(SdQueue.queue_priority.desc(), SdQueue.name).all()
    for wanted in (subcategory_id, category_id):
        if not wanted:
            continue
        w = str(wanted)
        for qz in queues:
            if w in [str(x) for x in (qz.category_ids or [])]:
                return qz
    return None


def _find_queue_for_team(db, team_id) -> SdQueue | None:
    """The team's own lane: the active queue whose crew is this team (highest
    drain priority first). Used to park team-routed tickets — the tier desks are
    queue-scoped, so a ticket with a team but no queue is invisible on every lane."""
    if not team_id:
        return None
    return (db.query(SdQueue)
            .filter(SdQueue.is_deleted == False, SdQueue.is_active == True,  # noqa: E712
                    SdQueue.team_id == team_id)
            .order_by(SdQueue.queue_priority.desc(), SdQueue.name)
            .first())


def _candidate_agents(db, queue: SdQueue) -> list:
    """The AGENT members of the queue's team (active + is_support_agent/superuser),
    kept in the team's member order so round-robin is deterministic. Auto-assign only
    ever targets agents — non-agent employees can be on the team for visibility, but a
    ticket is never auto-routed to someone who can't work it (a team with no agents
    simply leaves the ticket unassigned for triage)."""
    if not queue or not queue.team_id:
        return []
    team = db.query(SdTeam).filter(SdTeam.id == queue.team_id).first()
    if not team or not team.member_ids:
        return []
    order = [str(x) for x in team.member_ids]
    users = db.query(User).filter(User.id.in_(order), User.is_active == True).all()  # noqa: E712
    by_id = {str(u.id): u for u in users}
    ordered = [by_id[i] for i in order if i in by_id]
    return [u for u in ordered if getattr(u, "is_support_agent", False) or getattr(u, "is_superuser", False)]


def _round_robin(last_user_id, pool: list):
    """Next member after a cursor id (oldest-served-first rotation). Shared by
    queue- and team-based auto-assignment."""
    if not pool:
        return None
    last = str(last_user_id) if last_user_id else None
    idx = next((i for i, u in enumerate(pool) if str(u.id) == last), -1)
    return pool[(idx + 1) % len(pool)]


def _pick_round_robin(queue: SdQueue, pool: list):
    """Next member after the queue's cursor (oldest-served-first rotation)."""
    return _round_robin(queue.rr_last_user_id, pool)


def _agents_of_team(db, team: SdTeam) -> list:
    """The auto-assignable members of a team, kept in member order. Unlike queue
    routing (which requires the global is_support_agent flag), TEAM routing trusts the
    team roster: any member NOT explicitly tagged 'collaborator' in member_roles is
    workable. A team that is all collaborators (or empty) simply leaves the ticket
    unassigned in its queue for manual pick-up."""
    if not team or not team.member_ids:
        return []
    roles = team.member_roles or {}
    order = [str(x) for x in team.member_ids]
    workable = [uid for uid in order if roles.get(uid, "agent") != "collaborator"]
    if not workable:
        return []
    users = db.query(User).filter(User.id.in_(workable), User.is_active == True).all()  # noqa: E712
    by_id = {str(u.id): u for u in users}
    return [by_id[i] for i in workable if i in by_id]


def _find_team_for_type(db, ticket_type, category_id) -> SdTeam | None:
    """The active team that handles this ticket's request type (and/or category).
    Preference: a team matching BOTH type and category > type only > category only.
    Python-side filter — team counts are tiny and JSONB element containment is finicky
    across drivers (same rationale as the queue lookup above)."""
    tt = str(ticket_type or "")
    cid = str(category_id) if category_id else None
    if not tt and not cid:
        return None
    teams = db.query(SdTeam).filter(
        SdTeam.is_active == True, SdTeam.is_deleted == False,  # noqa: E712
    ).order_by(SdTeam.name).all()
    both = type_only = cat_only = None
    for tm in teams:
        rts = [str(x) for x in (tm.request_types or [])]
        cats = [str(x) for x in (tm.category_ids or [])]
        type_match = bool(tt) and tt in rts
        cat_match = bool(cid) and cid in cats
        if type_match and cat_match and not both:
            both = tm
        elif type_match and not type_only:
            type_only = tm
        elif cat_match and not cat_only:
            cat_only = tm
    return both or type_only or cat_only


def teams_handling(db, ticket_type, category_id=None) -> list[SdTeam]:
    """EVERY active team that declares this request type and/or category (any match) —
    the full set a requester could legitimately belong to. `_find_team_for_type` picks
    ONE team for routing; this returns all candidates so a membership *gate* isn't fooled
    when two teams (e.g. Tier 1 + Tier 2) both handle the same type — being on any one of
    them must count."""
    tt = str(ticket_type or "")
    cid = str(category_id) if category_id else None
    if not tt and not cid:
        return []
    teams = db.query(SdTeam).filter(
        SdTeam.is_active == True, SdTeam.is_deleted == False,  # noqa: E712
    ).order_by(SdTeam.name).all()
    out = []
    for tm in teams:
        rts = [str(x) for x in (tm.request_types or [])]
        cats = [str(x) for x in (tm.category_ids or [])]
        if (tt and tt in rts) or (cid and cid in cats):
            out.append(tm)
    return out


def _pick_load_balanced(db, pool: list, max_load: int | None = None):
    """The member with the fewest open (non-terminal) tickets; ties break on pool order.
    ``max_load`` (queue capacity cap) drops members at/over the cap first — SOFT cap:
    if everyone is capped, the least-loaded member still wins (fail-open, the queue
    must never wedge on capacity data)."""
    if not pool:
        return None
    ids = [u.id for u in pool]
    rows = (db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
            .filter(SdTicket.assigned_agent_id.in_(ids), SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status.notin_(TERMINAL_TICKET_STATUSES))
            .group_by(SdTicket.assigned_agent_id).all())
    counts = {str(r[0]): r[1] for r in rows}
    if max_load and int(max_load) > 0:
        under = [u for u in pool if counts.get(str(u.id), 0) < int(max_load)]
        if under:
            pool = under
    return min(pool, key=lambda u: counts.get(str(u.id), 0))


def match_route(db, t: SdTicket) -> dict:
    """READ-ONLY: which queue + team WOULD this ticket route to? Mirrors the FULL
    creation chain in order — explicit queue → automation rules (dry run) →
    queue-by-category → team-by-type+category → park in the team's own lane — but
    mutates NOTHING: no queue_id/team_id writes, no activity rows, no auto-assign.
    Powers the create-page routing preview so the agent sees the destination team +
    SLA before the ticket exists. The rule simulation matters: the real create path
    runs `evaluate_rules` BEFORE the category router, so a preview that skips the
    rules can promise one team while a routing rule delivers another. Returns
    {queue, team} (ORM objects or None). Best-effort — never raises."""
    queue = team = None
    try:
        if t.queue_id:
            queue = db.query(SdQueue).filter(SdQueue.id == t.queue_id, SdQueue.is_deleted == False).first()  # noqa: E712
        if not queue:
            # Dry-run the on_create rule chain — first-match wins on the real path too.
            import uuid as _uuid
            from app.utils.support_desk.rules import evaluate_rules  # local: avoid import cycle
            decision = (evaluate_rules(db, t, trigger="on_create", dry_run=True) or {}).get("decision") or {}
            if decision.get("queue_id"):
                queue = db.query(SdQueue).filter(SdQueue.id == _uuid.UUID(decision["queue_id"]), SdQueue.is_deleted == False).first()  # noqa: E712
            if decision.get("team_id"):
                team = db.query(SdTeam).filter(SdTeam.id == _uuid.UUID(decision["team_id"]), SdTeam.is_deleted == False).first()  # noqa: E712
        if not queue:
            queue = _find_queue_for_category(db, t.category_id, t.subcategory_id)
        # Resolved team: an explicit team wins, else the rules' team, else the queue's
        # team, else type/category match.
        if t.team_id:
            team = db.query(SdTeam).filter(SdTeam.id == t.team_id, SdTeam.is_deleted == False).first()  # noqa: E712
        if not team and queue and queue.team_id:
            team = db.query(SdTeam).filter(SdTeam.id == queue.team_id, SdTeam.is_deleted == False).first()  # noqa: E712
        if not team:
            team = _find_team_for_type(db, t.ticket_type, t.category_id)
        # Park preview: a team-routed ticket lands in the team's own lane (step 4 of
        # route_and_assign) — show that lane instead of "no queue".
        if not queue and team:
            queue = _find_queue_for_team(db, team.id)
        # Capacity preview: mirror the overflow hop so the promise matches the route.
        if queue is not None:
            queue, _hopped = apply_overflow(db, queue)
    except Exception:
        pass
    return {"queue": queue, "team": team}


def route_and_assign(db, t: SdTicket) -> dict:
    """Route the ticket to a queue and (if configured) auto-assign an agent.
    Returns {queue_id, queue_name, assigned_user_id} — all optional. Best-effort."""
    out = {"queue_id": None, "queue_name": None, "assigned_user_id": None}
    try:
        # 1) ROUTE — honour an explicit queue, else derive from the category map.
        #    Either way the capacity gate runs: a full lane spills ONE hop to its
        #    overflow target (creation-time routing only — manual moves never spill).
        queue = None
        if t.queue_id:
            queue = db.query(SdQueue).filter(SdQueue.id == t.queue_id, SdQueue.is_deleted == False).first()  # noqa: E712
        if not queue:
            queue = _find_queue_for_category(db, t.category_id, t.subcategory_id)
        if queue:
            queue, hopped = apply_overflow(db, queue)
            t.queue_id = queue.id
            if queue.team_id and not t.team_id:
                t.team_id = queue.team_id
            out["queue_id"] = queue.id
            out["queue_name"] = queue.name
            detail = {"queue": queue.name}
            if hopped:
                detail["by"] = "overflow"
            db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=None, actor_name="Routing",
                                    action="routed", detail=detail))

        # 2) ASSIGN — only if the queue auto-assigns, method != manual, and it's unassigned.
        if (queue and queue.auto_assign and (queue.assignment_method or "round_robin") != "manual"
                and not t.assigned_agent_id):
            # Skill gate → availability gate, both FAIL-OPEN (see the helpers).
            pool = _filter_available(db, _filter_skilled(db, queue, _candidate_agents(db, queue)))
            if pool:
                method = queue.assignment_method or "round_robin"
                picked = (_pick_load_balanced(db, pool, getattr(queue, "max_agent_load", None))
                          if method == "load_balanced" else _pick_round_robin(queue, pool))
                if picked:
                    t.assigned_agent_id = picked.id
                    queue.rr_last_user_id = picked.id   # advance the cursor for both methods
                    out["assigned_user_id"] = picked.id
                    db.add(SdTicketActivity(
                        ticket_id=t.id, actor_user_id=None, actor_name="Auto-assign",
                        action="assigned",
                        detail={"assigned_agent_id": str(picked.id), "method": method, "queue": queue.name, "auto": True}))

        # 3) TEAM ROUTING — when no queue claimed a team, route by the ticket's request
        #    TYPE (and/or category) to the team that declares it. Then, if that team is
        #    set to auto-assign, claim a member (round-robin / load-balanced).
        #    NOTE: when a QUEUE already claimed the ticket (rule- or category-routed),
        #    the team router may still fill team_id for the seal, but the queue's own
        #    assignment policy governs — a manual lane must not be drained by another
        #    team's auto-assign (the e2e probe caught exactly this).
        if not t.team_id:
            team = _find_team_for_type(db, t.ticket_type, t.category_id)
            if team:
                t.team_id = team.id
                out["team_id"] = team.id
                out["team_name"] = team.name
                db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=None, actor_name="Routing",
                                        action="routed", detail={"team": team.name, "by": "request_type"}))
                if (queue is None and team.auto_assign
                        and (team.assignment_method or "round_robin") != "manual"
                        and not t.assigned_agent_id):
                    pool = _filter_available(db, _agents_of_team(db, team))
                    if pool:
                        method = team.assignment_method or "round_robin"
                        picked = _pick_load_balanced(db, pool) if method == "load_balanced" else _round_robin(team.rr_last_user_id, pool)
                        if picked:
                            t.assigned_agent_id = picked.id
                            team.rr_last_user_id = picked.id
                            out["assigned_user_id"] = picked.id
                            db.add(SdTicketActivity(
                                ticket_id=t.id, actor_user_id=None, actor_name="Auto-assign",
                                action="assigned",
                                detail={"assigned_agent_id": str(picked.id), "method": method, "team": team.name, "auto": True}))

        # 4) PARK — a ticket that holds a team but no queue is invisible on every tier
        #    desk (the lane grids are queue-scoped), so "routed to Tier 2" would never
        #    surface on the L2 board. Park it in the team's own lane. Parking is
        #    placement only — the lane's assignment policy was already honoured above,
        #    so a manual lane stays manual.
        if t.team_id and not t.queue_id:
            team_queue = _find_queue_for_team(db, t.team_id)
            if team_queue:
                team_queue, hopped = apply_overflow(db, team_queue)
                t.queue_id = team_queue.id
                out["queue_id"] = team_queue.id
                out["queue_name"] = team_queue.name
                db.add(SdTicketActivity(
                    ticket_id=t.id, actor_user_id=None, actor_name="Routing",
                    action="routed",
                    detail={"queue": team_queue.name, "by": "overflow" if hopped else "team_queue"}))
                queue = queue or team_queue

        # 5) SLA POLICY — the lane that finally holds the ticket may carry its own
        #    SLA package (config v2). Applied last so overflow hops re-class too.
        if t.queue_id:
            final_q = queue if (queue and str(queue.id) == str(t.queue_id)) else (
                db.query(SdQueue).filter(SdQueue.id == t.queue_id).first())
            if final_q is not None:
                apply_queue_sla(db, t, final_q)
    except Exception:
        # Routing must never break ticket creation.
        pass
    return out
