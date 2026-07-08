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
from app.models.support_desk.workspace import SdTeam, SdQueue
from app.models.support_desk.constants import TERMINAL_TICKET_STATUSES


def _find_queue_for_category(db, category_id) -> SdQueue | None:
    """The first active queue that routes this category (Python filter — queue counts
    are tiny and JSONB element-containment operators are finicky across drivers)."""
    if not category_id:
        return None
    cid = str(category_id)
    queues = db.query(SdQueue).filter(
        SdQueue.is_active == True, SdQueue.is_deleted == False,  # noqa: E712
    ).order_by(SdQueue.name).all()
    for qz in queues:
        if cid in [str(x) for x in (qz.category_ids or [])]:
            return qz
    return None


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


def _pick_load_balanced(db, pool: list):
    """The member with the fewest open (non-terminal) tickets; ties break on pool order."""
    if not pool:
        return None
    ids = [u.id for u in pool]
    rows = (db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
            .filter(SdTicket.assigned_agent_id.in_(ids), SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status.notin_(TERMINAL_TICKET_STATUSES))
            .group_by(SdTicket.assigned_agent_id).all())
    counts = {str(r[0]): r[1] for r in rows}
    return min(pool, key=lambda u: counts.get(str(u.id), 0))


def match_route(db, t: SdTicket) -> dict:
    """READ-ONLY: which queue + team WOULD this ticket route to? Mirrors the *matching*
    half of route_and_assign (queue-by-category → team; team-by-type+category fallback)
    using the same `_find_*` helpers, but mutates NOTHING — no queue_id/team_id writes,
    no activity rows, no auto-assign. Powers the create-page routing preview so the
    agent sees the destination team + SLA before the ticket exists. Returns
    {queue, team} (ORM objects or None). Best-effort — never raises."""
    queue = team = None
    try:
        if t.queue_id:
            queue = db.query(SdQueue).filter(SdQueue.id == t.queue_id, SdQueue.is_deleted == False).first()  # noqa: E712
        if not queue:
            queue = _find_queue_for_category(db, t.category_id)
        # Resolved team: an explicit team wins, else the queue's team, else type/category match.
        if t.team_id:
            team = db.query(SdTeam).filter(SdTeam.id == t.team_id, SdTeam.is_deleted == False).first()  # noqa: E712
        if not team and queue and queue.team_id:
            team = db.query(SdTeam).filter(SdTeam.id == queue.team_id, SdTeam.is_deleted == False).first()  # noqa: E712
        if not team:
            team = _find_team_for_type(db, t.ticket_type, t.category_id)
    except Exception:
        pass
    return {"queue": queue, "team": team}


def route_and_assign(db, t: SdTicket) -> dict:
    """Route the ticket to a queue and (if configured) auto-assign an agent.
    Returns {queue_id, queue_name, assigned_user_id} — all optional. Best-effort."""
    out = {"queue_id": None, "queue_name": None, "assigned_user_id": None}
    try:
        # 1) ROUTE — honour an explicit queue, else derive from the category map.
        queue = None
        if t.queue_id:
            queue = db.query(SdQueue).filter(SdQueue.id == t.queue_id, SdQueue.is_deleted == False).first()  # noqa: E712
        if not queue:
            queue = _find_queue_for_category(db, t.category_id)
            if queue:
                t.queue_id = queue.id
        if queue:
            if queue.team_id and not t.team_id:
                t.team_id = queue.team_id
            out["queue_id"] = queue.id
            out["queue_name"] = queue.name
            db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=None, actor_name="Routing",
                                    action="routed", detail={"queue": queue.name}))

        # 2) ASSIGN — only if the queue auto-assigns, method != manual, and it's unassigned.
        if (queue and queue.auto_assign and (queue.assignment_method or "round_robin") != "manual"
                and not t.assigned_agent_id):
            pool = _candidate_agents(db, queue)
            if pool:
                method = queue.assignment_method or "round_robin"
                picked = _pick_load_balanced(db, pool) if method == "load_balanced" else _pick_round_robin(queue, pool)
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
        if not t.team_id:
            team = _find_team_for_type(db, t.ticket_type, t.category_id)
            if team:
                t.team_id = team.id
                out["team_id"] = team.id
                out["team_name"] = team.name
                db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=None, actor_name="Routing",
                                        action="routed", detail={"team": team.name, "by": "request_type"}))
                if (team.auto_assign and (team.assignment_method or "round_robin") != "manual"
                        and not t.assigned_agent_id):
                    pool = _agents_of_team(db, team)
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
    except Exception:
        # Routing must never break ticket creation.
        pass
    return out
