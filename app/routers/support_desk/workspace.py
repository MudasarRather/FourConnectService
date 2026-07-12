"""Support Desk — Phase-3 workspace routers: Teams, Queues, Saved Views, Templates.

Teams/Queues/Templates: list = support agents (pickers + boards), mutations = superuser.
Saved Views: per-user (any authenticated user owns their own + sees shared).
"""
from __future__ import annotations

from datetime import timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy import or_, and_, func, case
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.user import User
from app.models.support_desk.workspace import SdTeam, SdQueue, SdSavedView, SdTicketTemplate
from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.constants import (
    TicketStatus, OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES,
    EVT_TICKET_ASSIGNED, EVT_TEAM_MEMBER_ADDED, EVT_TEAM_MEMBER_REMOVED, EVT_TEAM_LEAD_ASSIGNED,
)
from app.models.hr.employee import Employee
from app.utils.hr.lifecycle_guard import SEPARATED
from app.schemas.support_desk.workspace import (
    TeamCreate, TeamUpdate, TeamResponse,
    MemberImpactEntry, MemberImpactResponse,
    TeamFlowPoint, TeamOverviewCard, TeamsOverviewResponse,
    QueueCreate, QueueUpdate, QueueResponse,
    SavedViewCreate, SavedViewUpdate, SavedViewResponse,
)
from app.schemas.support_desk.ticket import (
    TeamQueueStats, TeamDistributeRequest, TeamDistributeResult, TicketListResponse,
)
from app.utils.dependencies import get_current_superuser, get_support_agent, get_current_user
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.utils.support_desk.team_ops import team_ops_conds, team_on_shift


def _as_uuid(v):
    try:
        return v if isinstance(v, UUID) else UUID(str(v))
    except Exception:
        return None


def _user_names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {str(r[0]): r[1] for r in db.query(User.id, User.full_name).filter(User.id.in_(ids)).all()}


def _agent_user_ids(db: Session, ids: set) -> set:
    """Subset of the given user ids that are support agents / superusers (badge use)."""
    ids = {i for i in ids if i}
    if not ids:
        return set()
    rows = db.query(User.id).filter(
        User.id.in_(ids),
        or_(User.is_support_agent == True, User.is_superuser == True),  # noqa: E712
    ).all()
    return {str(r[0]) for r in rows}


def _grant_member_agents(db: Session, member_ids, member_roles) -> int:
    """A support team is a roster of people who WORK the desk, so adding someone to a
    team must make them a support agent — otherwise they land on the team but the
    operational /support-desk/tickets/* endpoints 403 them and the agent console can't
    load. We grant `is_support_agent` to every member EXCEPT those explicitly tagged
    'collaborator' (collaborators are visibility-only). Grant-only — never revokes
    (removing from a team doesn't strip a flag a superuser/admin may rely on; use
    enable_support_agent.py --off for that)."""
    roles = member_roles or {}
    want = [_as_uuid(m) for m in (member_ids or []) if roles.get(str(m)) != "collaborator"]
    want = [i for i in want if i]
    if not want:
        return 0
    return (db.query(User)
            .filter(User.id.in_(want), User.is_support_agent == False)  # noqa: E712
            .update({User.is_support_agent: True}, synchronize_session=False))


def _normalize_lead(data: dict, existing=None) -> None:
    """Keep SdTeam's two lead definitions in lockstep on every write. If lead_user_id is
    empty but member_roles designates a 'lead', promote that user to lead_user_id; and
    always mirror lead_user_id back into member_roles as 'lead'. This closes the drift
    that left Tier 2 with a member_roles lead but a NULL lead_user_id — invisible to the
    actor gates (which resolve lead via tickets_self._is_lead, now honoring both).
    Mutates ``data`` in place; only touches the two keys when there's something to sync."""
    roles = data.get("member_roles")
    if roles is None:
        roles = dict((existing.member_roles or {}) if existing is not None else {})
    else:
        roles = dict(roles)
    if "lead_user_id" in data:
        lead = data.get("lead_user_id")
    elif existing is not None:
        lead = existing.lead_user_id
    else:
        lead = None
    lead = str(lead) if lead else None
    if not lead:
        for uid, role in roles.items():
            if role == "lead":
                lead = str(uid)
                break
    if lead:
        if roles.get(lead) != "lead":
            roles[lead] = "lead"
            data["member_roles"] = roles
        data["lead_user_id"] = lead


def _open_by_team(db: Session, team_ids: set) -> dict:
    team_ids = {i for i in team_ids if i}
    if not team_ids:
        return {}
    rows = (db.query(SdTicket.team_id, func.count(SdTicket.id))
            .filter(SdTicket.team_id.in_(team_ids), SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.status.in_(OPEN_TICKET_STATUSES))
            .group_by(SdTicket.team_id).all())
    return {str(r[0]): r[1] for r in rows}


def _active_by_team(db: Session, team_id) -> dict:
    """ACTIVE = non-terminal work, merged tombstones excluded. This is the guard set for
    deactivate/delete — `on_hold` is non-terminal (it was the loophole: OPEN_TICKET_STATUSES
    excludes it, so a team whose only live work was on hold could be deleted, stranding it)."""
    row = (db.query(
        func.count(SdTicket.id),
        func.sum(case((SdTicket.status.in_(list(OPEN_TICKET_STATUSES)), 1), else_=0)),
        func.sum(case((SdTicket.status == TicketStatus.ON_HOLD.value, 1), else_=0)))
        .filter(SdTicket.team_id == team_id, SdTicket.is_deleted == False,  # noqa: E712
                SdTicket.merged_into_id.is_(None),
                SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
        .first())
    total, open_n, hold_n = (int(x or 0) for x in (row or (0, 0, 0)))
    return {"total": total, "open": open_n, "on_hold": hold_n, "other": max(0, total - open_n - hold_n)}


def _escalated_in_active(db: Session, team_id) -> int:
    """Active tickets escalated INTO this team (functional escalation seam) — a live work
    pointer, so it blocks delete even when the ticket's own team_id is elsewhere."""
    return (db.query(SdTicket)
            .filter(SdTicket.escalated_to_team_id == team_id,
                    SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None),
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
            .count())


def _member_open_assignments(db: Session, team_id, user_ids) -> list[dict]:
    """Active team tickets still owned by each of the given members — one grouped query.
    Feeds both the PATCH member-removal 409 payload and the /member-impact preflight."""
    ids = [_as_uuid(u) for u in (user_ids or [])]
    ids = [i for i in ids if i]
    if not ids:
        return []
    rows = (db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
            .filter(SdTicket.team_id == team_id,
                    SdTicket.assigned_agent_id.in_(ids),
                    SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None),
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
            .group_by(SdTicket.assigned_agent_id).all())
    if not rows:
        return []
    names = _user_names(db, {r[0] for r in rows})
    return [{"user_id": str(r[0]), "name": names.get(str(r[0])), "open_count": int(r[1] or 0)}
            for r in sorted(rows, key=lambda r: -int(r[1] or 0))]


def _validate_members(db: Session, member_ids) -> list[str]:
    """The user ids in the roster that do NOT resolve to an existing, active user.
    (Employee row not required — a superuser lead may have no Employee record.)"""
    want = {str(m) for m in (member_ids or [])}
    ids = [_as_uuid(m) for m in want]
    ids = [i for i in ids if i]
    found = {str(r[0]) for r in db.query(User.id).filter(
        User.id.in_(ids), User.is_active == True).all()} if ids else set()  # noqa: E712
    return sorted(want - found)


def _notify_team_event(db: Session, event: str, user_ids, *, title: str, message: str,
                       actor_id=None, action_url: str = "/user/support/tickets/team") -> None:
    """Best-effort roster notifications (member added/removed, lead assigned). The actor
    is skipped — you don't need a bell for the change you just made. Caller commits."""
    try:
        from app.utils.hr.notify import dispatch
        for uid in {str(u) for u in (user_ids or []) if u}:
            if actor_id is not None and uid == str(actor_id):
                continue
            dispatch(db, event, uid, context={
                "title": title, "message": message, "action_url": action_url,
                "related_user_id": str(actor_id) if actor_id else None,
            }, audience="SUPPORT")
    except Exception:  # pragma: no cover — a notification must never break the mutation
        pass


def _guard_409(error: str, message: str, **extra):
    """Structured 409 detail — always carries `message` so a legacy string renderer
    degrades gracefully. The Team Command frontend keys off `error`."""
    return HTTPException(status_code=409, detail={"error": error, "message": message, **extra})


# ═══════════════════════ Teams ═══════════════════════
teams_router = APIRouter(prefix="/support-desk/teams", tags=["Support Desk — Teams"])


@teams_router.get("/", response_model=List[TeamResponse])
def list_teams(include_inactive: bool = False, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    q = db.query(SdTeam).filter(SdTeam.is_deleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(SdTeam.is_active == True)  # noqa: E712
    teams = q.order_by(SdTeam.name).all()
    # one batched name lookup across leads + all members
    everyone = set()
    for t in teams:
        everyone |= {_as_uuid(m) for m in (t.member_ids or [])}
        if t.lead_user_id:
            everyone.add(t.lead_user_id)
    names = _user_names(db, everyone)
    agent_ids = _agent_user_ids(db, everyone)
    open_counts = _open_by_team(db, {t.id for t in teams})
    for t in teams:
        roles = t.member_roles or {}
        t.lead_name = names.get(str(t.lead_user_id)) if t.lead_user_id else None
        t.member_count = len(t.member_ids or [])
        t.open_ticket_count = open_counts.get(str(t.id), 0)
        t.members = [{
            "id": str(m), "name": names.get(str(m)) or "Member",
            "role": ("lead" if str(m) == str(t.lead_user_id) else roles.get(str(m), "agent")),
            "is_agent": str(m) in agent_ids,
        } for m in (t.member_ids or [])]
    return teams


@teams_router.get("/agents")
def list_agents(db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """The flagged-agent pool — active users with is_support_agent / superuser.
    (Kept for assignee pickers.) Declared before the /{team_id} mutation routes."""
    rows = db.query(User).filter(
        User.is_active == True,  # noqa: E712
        or_(User.is_support_agent == True, User.is_superuser == True),  # noqa: E712
    ).order_by(User.full_name).all()
    return [{"id": str(u.id), "name": u.full_name or u.email or "Agent", "email": u.email,
             "is_agent": bool(getattr(u, "is_support_agent", False)), "is_superuser": bool(u.is_superuser)} for u in rows]


@teams_router.get("/people")
def list_people(q: Optional[str] = None, limit: int = Query(400, ge=1, le=1000),
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """The full employee directory for building a team — active employees enriched
    with designation / department + agent / manager badges. The admin picks team
    MEMBERS from here (employees), not just flagged agents."""
    # Directory seal: superusers browse the whole directory (team building); agents
    # get a SEARCH, not a dump — a 2+ character name/email query, capped at 25 rows
    # (the agent-side pickers are typeaheads). Without this any flagged agent could
    # enumerate every employee's name + email + department in a single call.
    if not getattr(admin, "is_superuser", False):
        if not q or len(q.strip()) < 2:
            raise HTTPException(422, "Type at least 2 characters to search people.")
        limit = min(limit, 25)
    # Who is a reporting manager? (distinct managers across active employees.)
    mgr_rows = (db.query(Employee.reporting_manager_id)
                .filter(Employee.reporting_manager_id.isnot(None), Employee.is_deleted == False)  # noqa: E712
                .distinct().all())
    mgr_ids = {str(r[0]) for r in mgr_rows if r[0]}

    query = (db.query(Employee)
             .join(User, User.id == Employee.user_id)
             .options(joinedload(Employee.user), joinedload(Employee.department), joinedload(Employee.designation))
             .filter(Employee.is_deleted == False, User.is_active == True,  # noqa: E712
                     or_(Employee.lifecycle_state.is_(None), Employee.lifecycle_state.notin_(SEPARATED))))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(User.full_name.ilike(like), User.email.ilike(like)))
    rows = query.order_by(User.full_name).limit(limit).all()

    out = []
    for e in rows:
        u = e.user
        if not u:
            continue
        out.append({
            "id": str(u.id),
            "employee_code": e.employee_id,
            "name": u.full_name or u.email or "Employee",
            "email": u.email,
            "department": e.department.name if e.department else None,
            "designation": e.designation.name if e.designation else None,
            "is_agent": bool(getattr(u, "is_support_agent", False) or u.is_superuser),
            "is_manager": str(u.id) in mgr_ids,
        })
    return out


@teams_router.get("/mine")
def my_teams(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Support teams the current user belongs to (member or lead) — powers the
    user-panel 'my team's tickets' board. Any authenticated employee."""
    from app.routers.support_desk.tickets_self import _is_lead
    teams = db.query(SdTeam).filter(SdTeam.is_deleted == False, SdTeam.is_active == True).all()  # noqa: E712
    uid = str(user.id)
    mine = [t for t in teams if _is_lead(t, user.id) or uid in [str(m) for m in (t.member_ids or [])]]
    # resolve member + lead names in one pass
    all_ids = set()
    for t in mine:
        all_ids |= {str(m) for m in (t.member_ids or [])}
        if t.lead_user_id:
            all_ids.add(str(t.lead_user_id))
    id_objs = set()
    for s in all_ids:
        try:
            id_objs.add(UUID(s))
        except Exception:
            pass
    names = _user_names(db, id_objs)
    open_counts = _open_by_team(db, {t.id for t in mine})
    return [{
        "id": str(t.id), "name": t.name, "code": t.code, "color": t.color,
        "lead_user_id": str(t.lead_user_id) if t.lead_user_id else None,
        "lead_name": names.get(str(t.lead_user_id)) if t.lead_user_id else None,
        "is_lead": _is_lead(t, user.id),
        "member_count": len(t.member_ids or []),
        "open_ticket_count": open_counts.get(str(t.id), 0),
        "members": [{"id": str(m), "name": names.get(str(m)) or "Member"} for m in (t.member_ids or [])],
    } for t in mine]


@teams_router.post("/", response_model=TeamResponse, status_code=status.HTTP_201_CREATED)
def create_team(payload: TeamCreate, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if payload.code:
        held = db.query(SdTeam).filter(SdTeam.code == payload.code).first()
        if held:
            raise HTTPException(400, "Team code already exists" +
                                (" (held by an archived team)" if held.is_deleted else ""))
    if payload.name and db.query(SdTeam).filter(
            func.lower(SdTeam.name) == payload.name.strip().lower(),
            SdTeam.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "Team name already exists")
    data = payload.model_dump(exclude_unset=True)
    # dedupe (order-preserving) + validate every roster id is a real, active user
    data["member_ids"] = list(dict.fromkeys(str(m) for m in (data.get("member_ids") or [])))
    bad = _validate_members(db, data["member_ids"] +
                            ([str(data["lead_user_id"])] if data.get("lead_user_id") else []))
    if bad:
        raise HTTPException(422, f"Not active users: {', '.join(bad)}")
    if data.get("category_ids") is not None:
        data["category_ids"] = [str(x) for x in data["category_ids"]]   # JSONB needs JSON-serializable
    if data.get("member_roles") is not None:
        data["member_roles"] = {str(k): v for k, v in data["member_roles"].items()
                                if str(k) in set(data["member_ids"])}
    _normalize_lead(data)
    team = SdTeam(**data)
    db.add(team)
    db.flush()
    # The LEAD works the desk too — grant is_support_agent alongside the members.
    roster = list(team.member_ids or []) + ([str(team.lead_user_id)] if team.lead_user_id else [])
    granted = _grant_member_agents(db, roster, team.member_roles)
    write_audit(db, entity_type="team", op="created", entity_id=team.id, actor_id=admin.id,
                request=request, details={"name": team.name, "code": team.code,
                                          "members": len(team.member_ids or []),
                                          "agents_granted": granted})
    _notify_team_event(db, EVT_TEAM_MEMBER_ADDED, team.member_ids, actor_id=admin.id,
                       title=f"You were added to support team {team.name}",
                       message=f"You are on the roster of {team.name}"
                               + (f" ({team.code})" if team.code else "") + ".")
    if team.lead_user_id:
        _notify_team_event(db, EVT_TEAM_LEAD_ASSIGNED, [team.lead_user_id], actor_id=admin.id,
                           title=f"You lead support team {team.name}",
                           message=f"You were assigned as the lead of {team.name}.")
    db.commit()
    db.refresh(team)
    return team


@teams_router.patch("/{team_id}", response_model=TeamResponse)
def update_team(team_id: UUID, payload: TeamUpdate, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    team = db.query(SdTeam).filter(SdTeam.id == team_id, SdTeam.is_deleted == False).first()  # noqa: E712
    if not team:
        raise HTTPException(404, "Team not found")
    update = payload.model_dump(exclude_unset=True)
    # Reassignment DIRECTIVES, not columns — pop before setattr.
    strategy = update.pop("reassign_strategy", None)
    reassign_to = update.pop("reassign_to", None)

    # ── uniqueness (code collision used to escape to a 500 IntegrityError) ──
    if update.get("code") and update["code"] != team.code:
        if db.query(SdTeam).filter(SdTeam.code == update["code"], SdTeam.id != team.id).first():
            raise HTTPException(400, "Team code already exists")
    if update.get("name") and db.query(SdTeam).filter(
            func.lower(SdTeam.name) == update["name"].strip().lower(),
            SdTeam.id != team.id, SdTeam.is_deleted == False).first():  # noqa: E712
        raise HTTPException(400, "Team name already exists")

    # ── roster normalization: dedupe, validate, strip stale roles ──
    if update.get("member_ids") is not None:
        update["member_ids"] = list(dict.fromkeys(str(m) for m in update["member_ids"]))
    if update.get("category_ids") is not None:
        update["category_ids"] = [str(x) for x in update["category_ids"]]
    if update.get("member_roles") is not None:
        keep = set(update["member_ids"] if update.get("member_ids") is not None
                   else [str(m) for m in (team.member_ids or [])])
        update["member_roles"] = {str(k): v for k, v in update["member_roles"].items() if str(k) in keep}
    check_ids = list(update.get("member_ids") or [])
    if update.get("lead_user_id"):
        check_ids.append(str(update["lead_user_id"]))
    bad = _validate_members(db, check_ids)
    if bad:
        raise HTTPException(422, f"Not active users: {', '.join(bad)}")

    # Keep lead_user_id ⇄ member_roles['lead'] in lockstep (see _normalize_lead). Runs
    # before the removal-guard below so its new_lead computation sees the synced value.
    _normalize_lead(update, existing=team)

    # ── deactivation guard: is_active=false must not strand live work (a deactivated
    #    team vanishes from every non-superuser scope, so its tickets go unworkable) ──
    if update.get("is_active") is False and team.is_active:
        counts = _active_by_team(db, team.id)
        if counts["total"]:
            raise _guard_409(
                "team_has_active_tickets", team_id=str(team.id), **counts,
                message=(f"Cannot deactivate this team: {counts['total']} active ticket(s) "
                         f"({counts['open']} open, {counts['on_hold']} on hold) still belong to it. "
                         "Resolve or move them first."))

    # ── member-removal guard: the OLD effective roster (members ∪ lead) minus the new one.
    #    Removed members who still own active team tickets would be silently orphaned —
    #    the ex-member keeps the tickets but drops off every team board. ──
    old_members = [str(m) for m in (team.member_ids or [])]
    old_lead = str(team.lead_user_id) if team.lead_user_id else None
    new_members = update["member_ids"] if update.get("member_ids") is not None else old_members
    new_lead = (str(update["lead_user_id"]) if update.get("lead_user_id")
                else (None if "lead_user_id" in update else old_lead))
    old_eff = set(old_members) | ({old_lead} if old_lead else set())
    new_eff = set(new_members) | ({new_lead} if new_lead else set())
    removed = sorted(old_eff - new_eff)
    added = sorted(set(new_members) - set(old_members))
    impact = _member_open_assignments(db, team.id, removed) if removed else []
    moved: list[dict] = []
    if impact and not strategy:
        raise _guard_409(
            "members_have_open_assignments", team_id=str(team.id),
            members=impact, total_open=sum(m["open_count"] for m in impact),
            message=(f"{len(impact)} member(s) being removed still own "
                     f"{sum(m['open_count'] for m in impact)} active ticket(s) on this team. "
                     "Pass reassign_strategy ('auto' | 'unassign' | 'reassign' + reassign_to)."))

    # apply the update (uncommitted — a raise below rolls everything back)
    was_active = bool(team.is_active)
    for k, v in update.items():
        setattr(team, k, v)

    # ── execute the reassignment directive over the orphaned tickets ──
    if impact and strategy:
        from app.utils.support_desk.assignment import _agents_of_team, _round_robin
        pool = _agents_of_team(db, team)
        if new_lead and all(str(u.id) != new_lead for u in pool):
            lead_user = db.query(User).filter(User.id == _as_uuid(new_lead),
                                              User.is_active == True).first()  # noqa: E712
            if lead_user:
                pool = pool + [lead_user]
        if strategy == "reassign":
            tgt = str(reassign_to) if reassign_to else None
            roles = team.member_roles or {}
            if (not tgt or tgt not in new_eff or roles.get(tgt) == "collaborator"
                    or _validate_members(db, [tgt])):
                raise HTTPException(422, "reassign_to must be an active, assignable member of the updated roster")
            pool = [u for u in pool if str(u.id) == tgt]
            if not pool:
                raise HTTPException(422, "reassign_to must be an active, assignable member of the updated roster")
        if strategy == "auto" and not pool:
            raise _guard_409("team_has_no_assignable_members", team_id=str(team.id),
                             message="The updated roster has no assignable members to take over the removed members' tickets.")
        removed_uuids = [_as_uuid(m["user_id"]) for m in impact]
        tickets = (db.query(SdTicket)
                   .filter(SdTicket.team_id == team.id,
                           SdTicket.assigned_agent_id.in_([i for i in removed_uuids if i]),
                           SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES))).all())
        method = team.assignment_method if team.assignment_method in ("round_robin", "load_balanced") else "round_robin"
        loads = {str(r[0]): int(r[1] or 0) for r in
                 db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
                 .filter(SdTicket.assigned_agent_id.in_([u.id for u in pool]),
                         SdTicket.is_deleted == False,  # noqa: E712
                         SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                 .group_by(SdTicket.assigned_agent_id).all()} if pool else {}
        cursor = team.rr_last_user_id
        actor = getattr(admin, "full_name", None) or "Administrator"
        for t in tickets:
            prev = t.assigned_agent_id
            if strategy == "unassign":
                t.assigned_agent_id = None
                action, det_to = "unassigned", None
            else:
                if strategy == "reassign":
                    picked = pool[0]
                elif method == "load_balanced":
                    picked = min(pool, key=lambda u: loads.get(str(u.id), 0))
                else:
                    picked = _round_robin(cursor, pool)
                    cursor = picked.id
                t.assigned_agent_id = picked.id
                loads[str(picked.id)] = loads.get(str(picked.id), 0) + 1
                action, det_to = "assigned", str(picked.id)
            db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=admin.id, actor_name=actor,
                                    action=action,
                                    detail={"by": "team_member_removed", "strategy": strategy,
                                            "from": str(prev) if prev else None, "to": det_to}))
            if det_to and det_to != (str(prev) if prev else None):
                _notify_team_event(db, EVT_TICKET_ASSIGNED, [det_to], actor_id=admin.id,
                                   title=f"Assigned to you: {t.subject}",
                                   message=f"Ticket {t.ticket_number} was moved to you when its previous owner left {team.name}.",
                                   action_url="/user/support/tickets/my")
            moved.append({"ticket": t.ticket_number, "from": str(prev) if prev else None, "to": det_to})
        if strategy == "auto" and method == "round_robin":
            team.rr_last_user_id = cursor

    # roster the admin builds must be able to work the desk (lead included, grant-only)
    if "member_ids" in update or "member_roles" in update or "lead_user_id" in update:
        roster = list(team.member_ids or []) + ([str(team.lead_user_id)] if team.lead_user_id else [])
        _grant_member_agents(db, roster, team.member_roles)

    # ── audit (update/deactivate/reactivate were previously untracked) ──
    names = _user_names(db, {_as_uuid(x) for x in (set(removed) | set(added) |
                                                   ({old_lead} if old_lead else set()) |
                                                   ({new_lead} if new_lead else set()))})
    op = ("deactivated" if (was_active and update.get("is_active") is False)
          else "reactivated" if (not was_active and update.get("is_active") is True)
          else "updated")
    write_audit(db, entity_type="team", op=op, entity_id=team.id, actor_id=admin.id,
                request=request, details={
                    "fields_changed": sorted(update.keys()),
                    "members_added": [{"id": m, "name": names.get(m)} for m in added],
                    "members_removed": [{"id": m, "name": names.get(m)} for m in removed],
                    "lead_from": names.get(old_lead) if old_lead != new_lead else None,
                    "lead_to": names.get(new_lead) if old_lead != new_lead else None,
                    "reassignment": ({"strategy": strategy, "tickets": len(moved)} if moved else None),
                })

    # ── roster notifications ──
    if added:
        _notify_team_event(db, EVT_TEAM_MEMBER_ADDED, added, actor_id=admin.id,
                           title=f"You were added to support team {team.name}",
                           message=f"You are on the roster of {team.name}"
                                   + (f" ({team.code})" if team.code else "") + ".")
    if removed:
        _notify_team_event(db, EVT_TEAM_MEMBER_REMOVED, removed, actor_id=admin.id,
                           title=f"You were removed from support team {team.name}",
                           message=f"You are no longer on the roster of {team.name}.")
    if new_lead and new_lead != old_lead:
        _notify_team_event(db, EVT_TEAM_LEAD_ASSIGNED, [new_lead], actor_id=admin.id,
                           title=f"You lead support team {team.name}",
                           message=f"You were assigned as the lead of {team.name}.")

    db.commit()
    db.refresh(team)
    return team


@teams_router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team(team_id: UUID, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    team = db.query(SdTeam).filter(SdTeam.id == team_id, SdTeam.is_deleted == False).first()  # noqa: E712
    if not team:
        raise HTTPException(404, "Team not found")
    # ACTIVE (non-terminal) work blocks delete — on_hold included (the old guard used
    # OPEN_TICKET_STATUSES, which excludes on_hold, so a parked team could be deleted
    # out from under its held tickets). Live escalations INTO this team block too.
    counts = _active_by_team(db, team.id)
    esc_in = _escalated_in_active(db, team.id)
    if counts["total"] or esc_in:
        raise _guard_409(
            "team_has_active_tickets", team_id=str(team.id), escalated_in=esc_in, **counts,
            message=(f"Cannot delete this team: {counts['total']} active ticket(s) "
                     f"({counts['open']} open, {counts['on_hold']} on hold)"
                     + (f" and {esc_in} live escalation(s) into it" if esc_in else "")
                     + " still depend on it."))
    # Detach routing references so a dead team can't keep receiving work: a queue or
    # template pointing here would stamp team_id on new tickets and strand them.
    queues = db.query(SdQueue).filter(SdQueue.team_id == team.id, SdQueue.is_deleted == False).all()  # noqa: E712
    tpls = db.query(SdTicketTemplate).filter(SdTicketTemplate.team_id == team.id,
                                             SdTicketTemplate.is_deleted == False).all()  # noqa: E712
    for qrow in queues:
        qrow.team_id = None
    for tpl in tpls:
        tpl.team_id = None
    # Historical team_id / escalated_to_team_id on terminal tickets stay pointing at the
    # soft-deleted row on purpose — names still resolve for the record.
    team.is_deleted = True
    team.is_active = False
    write_audit(db, entity_type="team", op="deleted", entity_id=team.id, actor_id=admin.id,
                request=request, details={"name": team.name, "code": team.code,
                                          "queues_detached": [str(q.id) for q in queues],
                                          "templates_detached": [str(t.id) for t in tpls]})
    db.commit()
    return None


# ─────────── Team Command (admin oversight desk) — overview + drill + guards ───────────

@teams_router.get("/overview", response_model=TeamsOverviewResponse)
def teams_overview(include_inactive: bool = False,
                   db: Session = Depends(get_db), agent: User = Depends(get_support_agent)):
    """One call powering the admin Team Command fleet board: per-team live counts (the
    SAME team_ops_conds lens math as the agent desk, so both panels reconcile), workload
    distribution, 7-day speed (pause-credited MTTR / FRT p50), 30-day CSAT, escalations-in,
    coverage, and a 7-day inflow/outflow spark. Superusers see the whole fleet; a support
    agent sees only the teams they are on. Fixed query budget (~10) — StaticPool-safe."""
    now = sla_util.now_utc()
    is_su = bool(getattr(agent, "is_superuser", False))
    q = db.query(SdTeam).filter(SdTeam.is_deleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(SdTeam.is_active == True)  # noqa: E712
    teams = q.order_by(SdTeam.name).all()
    if not is_su:
        uid = str(agent.id)
        teams = [t for t in teams
                 if uid == str(t.lead_user_id) or uid in [str(m) for m in (t.member_ids or [])]]
    out = TeamsOverviewResponse(generated_at=now, team_count=len(teams))
    ids = [t.id for t in teams]
    if not ids:
        return out

    # idempotent sweeps first so the physics are honest (same trio as team-queue/stats)
    try:
        from app.routers.support_desk._common import auto_resume_expired_holds, auto_close_due_tickets
        auto_resume_expired_holds(db)
        auto_close_due_tickets(db)
    except Exception:
        db.rollback()
    try:
        from app.utils.support_desk.breach import sweep_sla_breach_flags
        if sweep_sla_breach_flags(db, team_cond=SdTicket.team_id.in_(ids)):
            db.commit()
    except Exception:
        db.rollback()

    conds = team_ops_conds(now)
    terminal = list(TERMINAL_TICKET_STATUSES)
    d7, d30 = now - timedelta(days=7), now - timedelta(days=30)
    sod = now.replace(hour=0, minute=0, second=0, microsecond=0)
    d7s = sod - timedelta(days=6)
    base = (SdTicket.is_deleted == False,  # noqa: E712
            SdTicket.merged_into_id.is_(None),
            SdTicket.team_id.in_(ids))

    # Q1 — active conditional sums per team
    act = {r[0]: r for r in db.query(
        SdTicket.team_id,
        func.count(SdTicket.id),
        func.sum(case((and_(SdTicket.assigned_agent_id.is_(None),
                            SdTicket.status.in_(list(OPEN_TICKET_STATUSES))), 1), else_=0)),
        func.sum(case((conds["breach"], 1), else_=0)),
        func.sum(case((conds["due_soon"], 1), else_=0)),
        func.sum(case((conds["idle"], 1), else_=0)),
        func.sum(case((SdTicket.status == TicketStatus.ON_HOLD.value, 1), else_=0)),
        func.sum(case((conds["critical"], 1), else_=0)),
    ).filter(*base, SdTicket.status.notin_(terminal)).group_by(SdTicket.team_id).all()}

    # Q2 — live escalations INTO each team
    esc = {r[0]: int(r[1] or 0) for r in db.query(
        SdTicket.escalated_to_team_id, func.count(SdTicket.id))
        .filter(SdTicket.escalated_to_team_id.in_(ids),
                SdTicket.is_deleted == False,  # noqa: E712
                SdTicket.merged_into_id.is_(None),
                SdTicket.status.notin_(terminal))
        .group_by(SdTicket.escalated_to_team_id).all()}

    # Q3 — per-agent active load (min/max/avg computed over the workable roster incl. zeros)
    loads: dict = {}
    for tid, aid, n in db.query(SdTicket.team_id, SdTicket.assigned_agent_id, func.count(SdTicket.id)) \
            .filter(*base, SdTicket.status.notin_(terminal), SdTicket.assigned_agent_id.isnot(None)) \
            .group_by(SdTicket.team_id, SdTicket.assigned_agent_id).all():
        loads.setdefault(tid, {})[str(aid)] = int(n or 0)

    # Q4 — MTTR p50 (pause-credited) + resolved count, 7d
    ttr_min = (func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at)
               - SdTicket.sla_paused_ms / 1000.0) / 60.0
    mttr = {r[0]: r for r in db.query(
        SdTicket.team_id,
        func.percentile_cont(0.5).within_group(ttr_min.asc()),
        func.count(SdTicket.id))
        .filter(*base, SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d7)
        .group_by(SdTicket.team_id).all()}

    # Q5 — FRT p50, 7d
    frt_min = func.extract("epoch", SdTicket.first_responded_at - SdTicket.created_at) / 60.0
    frt = {r[0]: r[1] for r in db.query(
        SdTicket.team_id, func.percentile_cont(0.5).within_group(frt_min.asc()))
        .filter(*base, SdTicket.first_responded_at.isnot(None), SdTicket.first_responded_at >= d7)
        .group_by(SdTicket.team_id).all()}

    # Q6 — CSAT, 30d
    csat = {r[0]: (r[1], int(r[2] or 0)) for r in db.query(
        SdTicket.team_id, func.avg(SdTicket.csat_score), func.count(SdTicket.csat_score))
        .filter(*base, SdTicket.csat_score.isnot(None), SdTicket.updated_at >= d30)
        .group_by(SdTicket.team_id).all()}

    # Q7/Q8 — 7-day inflow / outflow buckets
    cday = func.date_trunc("day", SdTicket.created_at)
    inflow: dict = {}
    for tid, day, n in db.query(SdTicket.team_id, cday, func.count(SdTicket.id)) \
            .filter(*base, SdTicket.created_at >= d7s).group_by(SdTicket.team_id, cday).all():
        if day is not None:
            inflow.setdefault(tid, {})[day.date()] = int(n or 0)
    rday = func.date_trunc("day", SdTicket.resolved_at)
    outflow: dict = {}
    for tid, day, n in db.query(SdTicket.team_id, rday, func.count(SdTicket.id)) \
            .filter(*base, SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d7s) \
            .group_by(SdTicket.team_id, rday).all():
        if day is not None:
            outflow.setdefault(tid, {})[day.date()] = int(n or 0)

    # Q9 — lead names, one batch
    names = _user_names(db, {t.lead_user_id for t in teams if t.lead_user_id})

    days = [sod - timedelta(days=i) for i in range(6, -1, -1)]
    agents_on_deck: set = set()
    for t in teams:
        roles = t.member_roles or {}
        members = [str(m) for m in (t.member_ids or [])]
        workable = [m for m in members if roles.get(m, "agent") != "collaborator"]
        if t.lead_user_id and str(t.lead_user_id) not in workable:
            workable.append(str(t.lead_user_id))
        agents_on_deck |= set(workable)
        tl = loads.get(t.id, {})
        per = [tl.get(m, 0) for m in workable]
        a = act.get(t.id)
        mt = mttr.get(t.id)
        cs = csat.get(t.id)
        fin, fout = inflow.get(t.id, {}), outflow.get(t.id, {})
        out.teams.append(TeamOverviewCard(
            id=t.id, name=t.name, code=t.code, color=t.color, is_active=bool(t.is_active),
            auto_assign=bool(t.auto_assign), assignment_method=t.assignment_method or "round_robin",
            business_hours=t.business_hours if isinstance(t.business_hours, dict) else {},
            request_types=[str(x) for x in (t.request_types or [])],
            lead_user_id=t.lead_user_id, lead_name=names.get(str(t.lead_user_id)) if t.lead_user_id else None,
            member_count=len(members), agent_count=len(workable),
            coverage_open=team_on_shift(t.business_hours, now),
            open=int(a[1] or 0) if a else 0,
            unassigned=int(a[2] or 0) if a else 0,
            breached=int(a[3] or 0) if a else 0,
            due_soon=int(a[4] or 0) if a else 0,
            idle=int(a[5] or 0) if a else 0,
            on_hold=int(a[6] or 0) if a else 0,
            critical=int(a[7] or 0) if a else 0,
            escalated_in=esc.get(t.id, 0),
            load_min=min(per) if per else None,
            load_max=max(per) if per else None,
            load_avg=round(sum(per) / len(per), 2) if per else None,
            resolved_7d=int(mt[2] or 0) if mt else 0,
            mttr_p50_7d=round(max(0.0, float(mt[1])), 1) if mt and mt[1] is not None else None,
            frt_p50_7d=round(max(0.0, float(frt[t.id])), 1) if frt.get(t.id) is not None else None,
            csat_30d=round(float(cs[0]), 2) if cs and cs[0] is not None else None,
            csat_n_30d=cs[1] if cs else 0,
            flow=[TeamFlowPoint(day=d, inflow=fin.get(d.date(), 0), outflow=fout.get(d.date(), 0))
                  for d in days],
        ))

    # Q10 — fleet rollup for the hero console (one percentile over the whole scope)
    frow = db.query(
        func.percentile_cont(0.5).within_group(ttr_min.asc()),
        func.avg(SdTicket.csat_score)) \
        .filter(*base, SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= d7).first()
    cards = out.teams
    out.totals = {
        "teams": len(cards),
        "agents_on_deck": len(agents_on_deck),
        "open": sum(c.open for c in cards),
        "unassigned": sum(c.unassigned for c in cards),
        "breached": sum(c.breached for c in cards),
        "due_soon": sum(c.due_soon for c in cards),
        "idle": sum(c.idle for c in cards),
        "critical": sum(c.critical for c in cards),
        "on_hold": sum(c.on_hold for c in cards),
        "resolved_7d": sum(c.resolved_7d for c in cards),
        "uncovered": sum(1 for c in cards if c.coverage_open is False and c.open > 0),
        "orphaned": sum(1 for c in cards if c.agent_count == 0),
        "mttr_p50_7d": round(max(0.0, float(frow[0])), 1) if frow and frow[0] is not None else None,
        "csat_7d": round(float(frow[1]), 2) if frow and frow[1] is not None else None,
    }
    return out


@teams_router.get("/{team_id}/stats", response_model=TeamQueueStats)
def team_stats(team_id: UUID, db: Session = Depends(get_db), agent: User = Depends(get_support_agent)):
    """Admin drill for one team — a thin delegate to the agent desk's team_queue_stats
    (same roster/flow/leaderboard math, same seal), so the admin surface never has to
    call the /me/* self-router and the two panels can never drift apart."""
    from app.routers.support_desk.tickets_self import team_queue_stats
    return team_queue_stats(team_id=team_id, db=db, user=agent)


@teams_router.get("/{team_id}/tickets", response_model=TicketListResponse)
def team_tickets(
    team_id: UUID,
    lens: Optional[str] = Query(None, description="all|unassigned|mine|breaching|due_soon|idle|escalated|pending|critical"),
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    ticket_type: Optional[str] = None,
    assigned_agent_id: Optional[UUID] = None,
    q: Optional[str] = None,
    sort_by: str = Query("updated_at"),
    sort_dir: str = Query("desc"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    agent: User = Depends(get_support_agent),
):
    """Admin drill ledger for one team — delegates to the sealed team-queue list so the
    lens filters here reconcile EXACTLY with the /overview and /stats counts."""
    from app.routers.support_desk.tickets_self import list_team_queue
    return list_team_queue(team_id=team_id, lens=lens, status_f=status_f, priority=priority,
                           ticket_type=ticket_type, assigned_agent_id=assigned_agent_id, q=q,
                           sort_by=sort_by, sort_dir=sort_dir, page=page, limit=limit,
                           db=db, user=agent)


@teams_router.post("/{team_id}/rebalance", response_model=TeamDistributeResult)
def team_rebalance(team_id: UUID, request: Request, payload: Optional[dict] = None,
                   db: Session = Depends(get_db), agent: User = Depends(get_support_agent)):
    """Admin rebalance for one team — delegates to the audited distribute single-writer
    (lead-or-superuser gate, assignment_method + rr cursor honoured, capped, notifying).
    The path team_id is authoritative; the body may carry {max_tickets}."""
    from app.routers.support_desk.tickets_self import distribute_team_queue
    req = TeamDistributeRequest(team_id=team_id,
                                max_tickets=int((payload or {}).get("max_tickets") or 25))
    return distribute_team_queue(payload=req, request=request, db=db, user=agent)


@teams_router.get("/{team_id}/member-impact", response_model=MemberImpactResponse)
def team_member_impact(team_id: UUID, remove: str = Query(..., description="comma-separated user ids"),
                       db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Preflight for the team-edit modal: how many active team tickets does each member
    being removed still own? The same helper feeds the PATCH 409, so the warning the
    modal shows BEFORE saving always matches what the save would be blocked on."""
    team = db.query(SdTeam).filter(SdTeam.id == team_id, SdTeam.is_deleted == False).first()  # noqa: E712
    if not team:
        raise HTTPException(404, "Team not found")
    ids = []
    for part in (remove or "").split(","):
        part = part.strip()
        if not part:
            continue
        u = _as_uuid(part)
        if not u:
            raise HTTPException(422, f"'{part}' is not a valid user id")
        ids.append(u)
    rows = _member_open_assignments(db, team.id, ids)
    return MemberImpactResponse(
        team_id=team.id,
        total_open=sum(r["open_count"] for r in rows),
        members=[MemberImpactEntry(**r) for r in rows])


# ═══════════════════════ Queues ═══════════════════════
queues_router = APIRouter(prefix="/support-desk/queues", tags=["Support Desk — Queues"])


@queues_router.get("/", response_model=List[QueueResponse])
def list_queues(include_inactive: bool = False, db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    q = db.query(SdQueue).filter(SdQueue.is_deleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(SdQueue.is_active == True)  # noqa: E712
    queues = q.order_by(SdQueue.name).all()
    team_names = {str(r[0]): r[1] for r in db.query(SdTeam.id, SdTeam.name).filter(
        SdTeam.id.in_({x.team_id for x in queues if x.team_id})).all()} if any(x.team_id for x in queues) else {}
    from sqlalchemy import func
    open_rows = (db.query(SdTicket.queue_id, func.count(SdTicket.id))
                 .filter(SdTicket.queue_id.in_({x.id for x in queues}), SdTicket.is_deleted == False,  # noqa: E712
                         SdTicket.status.in_(OPEN_TICKET_STATUSES))
                 .group_by(SdTicket.queue_id).all()) if queues else []
    open_counts = {str(r[0]): r[1] for r in open_rows}
    # Team seal on the WORKLOAD numbers (the rows themselves stay desk-wide — cross-team
    # lanes are legitimate tier-escalation targets, but another crew's live backlog is
    # not an agent's to read): non-superusers get real counts only for lanes owned by a
    # team they're on; every other lane reads 0. Mirrors queue_ops._visible_queues.
    if not getattr(admin, "is_superuser", False):
        from app.routers.support_desk.tickets_self import _team_context
        _tids = {str(x) for x in _team_context(db, admin)["team_ids"]}
        _sealed = {str(x.id) for x in queues if x.team_id and str(x.team_id) in _tids}
        open_counts = {k: v for k, v in open_counts.items() if k in _sealed}
    for x in queues:
        x.team_name = team_names.get(str(x.team_id)) if x.team_id else None
        x.open_ticket_count = open_counts.get(str(x.id), 0)
    return queues


def _clear_other_defaults(db: Session, keep_id=None) -> None:
    """At most ONE default (fallback) queue — flipping one on flips the others off."""
    q = db.query(SdQueue).filter(SdQueue.is_default == True, SdQueue.is_deleted == False)  # noqa: E712
    if keep_id is not None:
        q = q.filter(SdQueue.id != keep_id)
    q.update({SdQueue.is_default: False}, synchronize_session=False)


def _active_in_queue(db: Session, queue_id) -> int:
    """ACTIVE = non-terminal work, merged tombstones excluded (delete/deactivate guard —
    same definition as the team guard, so on-hold work can't be stranded)."""
    return (db.query(SdTicket)
            .filter(SdTicket.queue_id == queue_id, SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None),
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
            .count())


def _validate_overflow(db: Session, data: dict, queue_id=None) -> None:
    """Config-v2 spill guards: the overflow target must be a different, existing lane,
    and must not already spill back into this one (A→B→A would bounce tickets — the
    engine only ever hops once, but the config should never encode a loop)."""
    of = data.get("overflow_queue_id")
    if of is None:
        return
    if queue_id is not None and str(of) == str(queue_id):
        raise HTTPException(422, "A lane can't overflow into itself — pick a different spill lane.")
    target = db.query(SdQueue).filter(SdQueue.id == of, SdQueue.is_deleted == False).first()  # noqa: E712
    if not target:
        raise HTTPException(422, "overflow_queue_id must name an existing lane.")
    if queue_id is not None and getattr(target, "overflow_queue_id", None) and str(target.overflow_queue_id) == str(queue_id):
        raise HTTPException(422, f"Overflow loop: '{target.name}' already spills into this lane — pick a different target for one of them.")


@queues_router.post("/", response_model=QueueResponse, status_code=status.HTTP_201_CREATED)
def create_queue(payload: QueueCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    if payload.code and db.query(SdQueue).filter(SdQueue.code == payload.code).first():
        raise HTTPException(400, "Queue code already exists")
    data = payload.model_dump(exclude_unset=True)
    data.pop("reassign_to", None)   # directive, not a column
    _validate_overflow(db, data)
    for jsonb_key in ("category_ids", "skill_ids"):
        if data.get(jsonb_key) is not None:
            data[jsonb_key] = [str(x) for x in data[jsonb_key]]   # JSONB needs JSON-serializable
    qrow = SdQueue(**data)
    db.add(qrow)
    db.flush()
    if qrow.is_default:
        _clear_other_defaults(db, keep_id=qrow.id)
    write_audit(db, entity_type="queue", op="created", entity_id=qrow.id, actor_id=admin.id, details={"name": qrow.name})
    db.commit()
    db.refresh(qrow)
    return qrow


@queues_router.patch("/{queue_id}", response_model=QueueResponse)
def update_queue(queue_id: UUID, payload: QueueUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    qrow = db.query(SdQueue).filter(SdQueue.id == queue_id, SdQueue.is_deleted == False).first()  # noqa: E712
    if not qrow:
        raise HTTPException(404, "Queue not found")
    data = payload.model_dump(exclude_unset=True)
    data.pop("reassign_to", None)   # directive for DELETE, ignored on update
    # The fallback queue must stay routable: it can't be deactivated or un-defaulted
    # while it IS the default (make another queue default first).
    if qrow.is_default and data.get("is_active") is False:
        raise HTTPException(409, "This is the default (fallback) queue — make another queue the default before deactivating it.")
    if qrow.is_default and data.get("is_default") is False:
        raise HTTPException(409, "Make another queue the default first — the desk needs exactly one fallback queue.")
    _validate_overflow(db, data, queue_id=qrow.id)
    for k, v in data.items():
        if k in ("category_ids", "skill_ids") and v is not None:
            v = [str(x) for x in v]   # JSONB needs JSON-serializable
        setattr(qrow, k, v)
    if data.get("is_default"):
        _clear_other_defaults(db, keep_id=qrow.id)
    write_audit(db, entity_type="queue", op="updated", entity_id=qrow.id, actor_id=admin.id,
                details={"name": qrow.name, "fields": sorted(data.keys())})
    db.commit()
    db.refresh(qrow)
    return qrow


@queues_router.delete("/{queue_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_queue(
    queue_id: UUID,
    reassign_to: Optional[UUID] = Query(None, description="Queue that inherits this queue's active tickets"),
    reason: Optional[str] = Query(None, max_length=300, description="Why the lane is being pulled — lands in the audit ledger"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    qrow = db.query(SdQueue).filter(SdQueue.id == queue_id, SdQueue.is_deleted == False).first()  # noqa: E712
    if not qrow:
        raise HTTPException(404, "Queue not found")
    if qrow.is_default:
        raise HTTPException(409, "The default (fallback) queue can't be deleted — make another queue the default first.")
    active = _active_in_queue(db, qrow.id)
    if active:
        if not reassign_to:
            raise HTTPException(409, f"This queue still holds {active} active ticket(s) — pass reassign_to with the queue that inherits them.")
        target = db.query(SdQueue).filter(
            SdQueue.id == reassign_to, SdQueue.is_deleted == False,  # noqa: E712
            SdQueue.is_active == True, SdQueue.id != qrow.id).first()  # noqa: E712
        if not target:
            raise HTTPException(422, "reassign_to must name a different, active queue.")
        # Inheriting a lane means inheriting its CREW: when the target lane belongs to
        # a (different) team, moved tickets take that team_id too — otherwise they end
        # up sealed to the old team while parked on the new team's board (queue/team
        # divergence, same discipline as apply_tier_move). A team-less target keeps
        # each ticket's existing team so the seal never widens by accident. Assignees
        # are left untouched (same as a manual PATCH lane move).
        _updates = {SdTicket.queue_id: target.id}
        if target.team_id:
            _updates[SdTicket.team_id] = target.team_id
        moved = (db.query(SdTicket)
                 .filter(SdTicket.queue_id == qrow.id, SdTicket.is_deleted == False,  # noqa: E712
                         SdTicket.merged_into_id.is_(None),
                         SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                 .update(_updates, synchronize_session=False))
        write_audit(db, entity_type="queue", op="tickets_reassigned", entity_id=qrow.id, actor_id=admin.id,
                    details={"to_queue": str(target.id), "moved": int(moved),
                             "to_team": str(target.team_id) if target.team_id else None,
                             "assignees_kept": True})
    qrow.is_deleted = True
    qrow.is_active = False
    # Release the unique code so a future queue can reuse it (soft-deleted rows
    # otherwise squat on the code forever).
    if qrow.code:
        qrow.code = f"{qrow.code}~{str(qrow.id)[:8]}"
    details = {"name": qrow.name}
    if reason and reason.strip():
        details["reason"] = reason.strip()
    write_audit(db, entity_type="queue", op="deleted", entity_id=qrow.id, actor_id=admin.id, details=details)
    db.commit()
    return None


# ═══════════════════════ Saved Views (per-user) ═══════════════════════
saved_views_router = APIRouter(prefix="/support-desk/saved-views", tags=["Support Desk — Saved Views"])


@saved_views_router.get("/", response_model=List[SavedViewResponse])
def list_saved_views(scope: Optional[str] = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = db.query(SdSavedView).filter(or_(SdSavedView.owner_user_id == user.id, SdSavedView.is_shared == True))  # noqa: E712
    if scope:
        q = q.filter(SdSavedView.scope == scope)
    return q.order_by(SdSavedView.name).all()


@saved_views_router.post("/", response_model=SavedViewResponse, status_code=status.HTTP_201_CREATED)
def create_saved_view(payload: SavedViewCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    v = SdSavedView(owner_user_id=user.id, **payload.model_dump(exclude_unset=True))
    db.add(v)
    db.commit()
    db.refresh(v)
    return v


@saved_views_router.patch("/{view_id}", response_model=SavedViewResponse)
def update_saved_view(view_id: UUID, payload: SavedViewUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    v = db.query(SdSavedView).filter(SdSavedView.id == view_id).first()
    if not v:
        raise HTTPException(404, "Saved view not found")
    if str(v.owner_user_id) != str(user.id) and not user.is_superuser:
        raise HTTPException(403, "Not your saved view")
    for k, val in payload.model_dump(exclude_unset=True).items():
        setattr(v, k, val)
    db.commit()
    db.refresh(v)
    return v


@saved_views_router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_view(view_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    v = db.query(SdSavedView).filter(SdSavedView.id == view_id).first()
    if not v:
        raise HTTPException(404, "Saved view not found")
    if str(v.owner_user_id) != str(user.id) and not user.is_superuser:
        raise HTTPException(403, "Not your saved view")
    db.delete(v)
    db.commit()
    return None


# ═══════════════════════ Ticket Templates ═══════════════════════
# Moved to app/routers/support_desk/templates.py ("Copperplate Studio") — the
# package __init__ imports templates_router from there; public prefix unchanged.
# SdTicketTemplate stays imported above: delete_team detaches team_id on templates.
