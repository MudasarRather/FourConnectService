"""Support Desk — Queue Engine routers (the Queues module backend).

Four routers, all aggregated in ``routers/support_desk/__init__.py``:

  • ``skills_router``        /support-desk/skills          — skill catalog + agent-skill roster
  • ``agent_status_router``  /support-desk/agent-status,
                             /support-desk/me/status       — unified agent availability
  • ``queue_ops_router``     /support-desk/queues/...      — overview analytics, per-queue
                             stats, tier working boards, play-mode serve-next
  • ``ticket_tier_router``   /support-desk/tickets/{id}/... — skip, tier-escalate, tier-descend

Sealing: every agent-readable surface is TEAM-SEALED — a non-superuser only sees
queues owned by teams they belong to (``_visible_queues``, built on the same
``_team_context`` the command-center seal uses). Mutations on tickets go through the
existing scope + owner-tier actor gates from ``tickets.py``. Config mutations
(skills) are superuser-only.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketActivity, SdTicketComment
from app.models.support_desk.workspace import (
    SdTeam, SdQueue, SdSkill, SdAgentStatus, SdTicketSkip, SdTicketViewer,
)
from app.models.support_desk.collab import SdTicketWorklog, SdTicketWatcher, SdSwarmSession
from app.models.support_desk.itil import SdProblem, SdChangeRequest
from app.models.support_desk.ops import SdAutomationRule
from app.models.support_desk.core import SdCategory
from app.models.support_desk.constants import (
    TicketStatus, PRIORITY_ORDER, OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES,
    SKIP_REASON_CODES, TIER_DESCEND_REASON_CODES,
    CommentAuthorKind, EscalationType, EVT_TICKET_ESCALATED, EVT_TICKET_ASSIGNED,
)
from app.schemas.support_desk.workspace import (
    QueueOverviewCard, QueueFlowPoint, TierFlowEdge, QueuesOverviewResponse,
    QueueStatsResponse, TierBoardResponse, ServeNextResponse, SkipCreate,
    TierEscalateRequest, TierDescendRequest,
    SkillCreate, SkillUpdate, SkillResponse,
    AgentStatusEntry, AgentStatusRosterResponse, MyStatusUpdate,
)
from app.schemas.support_desk.ticket import TicketResponse
from app.schemas.support_desk.ops import ConfigLedgerEntry, ConfigLedgerResponse
from app.utils.dependencies import get_current_superuser, get_support_agent
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.utils.support_desk.rca import rca_missing_legacy_cond as _rca_missing
from app.utils.support_desk.rules import find_tier_queue, apply_tier_move, sweep_time_based_rules
from app.utils.support_desk.team_ops import team_ops_conds, team_on_shift

# Statuses a play-mode serve can claim from — actively-workable, unpaused work.
_SERVE_STATUSES = [TicketStatus.OPEN.value, TicketStatus.IN_PROGRESS.value, TicketStatus.ESCALATED.value]
# A presence heartbeat younger than this marks the ticket "being viewed" (collision window).
_VIEWER_FRESH_SECONDS = 60


def _now():
    return sla_util.now_utc()


def _as_uuid(v):
    try:
        return v if isinstance(v, UUID) else UUID(str(v))
    except Exception:
        return None


def _problems_open(db: Session) -> int:
    """Problems still being worked (open/investigating) — desk-wide, not tier-scoped."""
    from app.models.support_desk.constants import ProblemStatus
    return int(db.query(func.count(SdProblem.id))
               .filter(SdProblem.is_deleted == False,  # noqa: E712
                       SdProblem.status.in_([ProblemStatus.OPEN.value,
                                             ProblemStatus.INVESTIGATING.value])).scalar() or 0)


def _known_errors(db: Session) -> int:
    """The Known-Error DB shelf: known_error status OR a published workaround."""
    from app.models.support_desk.constants import ProblemStatus
    return int(db.query(func.count(SdProblem.id))
               .filter(SdProblem.is_deleted == False,  # noqa: E712
                       or_(SdProblem.status == ProblemStatus.KNOWN_ERROR.value,
                           SdProblem.workaround_published == True)).scalar() or 0)  # noqa: E712


def _mins(delta_seconds: float | None) -> Optional[float]:
    if delta_seconds is None:
        return None
    return round(float(delta_seconds) / 60.0, 1)


def _my_logged_today(db: Session, user_id, day_start) -> int:
    """Minutes the caller logged today across the desk (per-entry worklogs only)."""
    return int(db.query(func.coalesce(func.sum(SdTicketWorklog.minutes), 0))
               .filter(SdTicketWorklog.user_id == user_id,
                       SdTicketWorklog.is_deleted == False,  # noqa: E712
                       SdTicketWorklog.created_at >= day_start).scalar() or 0)


def _visible_queues(db: Session, admin: User, *, include_inactive: bool = False,
                    tier: Optional[int] = None):
    """The queues this caller may see: superuser = whole desk; agent = queues owned by
    a team they're on (the team seal, queue flavour). Returns (queues, ctx|None)."""
    q = db.query(SdQueue).filter(SdQueue.is_deleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(SdQueue.is_active == True)  # noqa: E712
    if tier is not None:
        q = q.filter(SdQueue.tier == int(tier))
    queues = q.order_by(SdQueue.queue_priority.desc(), SdQueue.name).all()
    if getattr(admin, "is_superuser", False):
        return queues, None
    from app.routers.support_desk.tickets_self import _team_context
    ctx = _team_context(db, admin)
    tids = {str(x) for x in ctx["team_ids"]}
    return [x for x in queues if x.team_id and str(x.team_id) in tids], ctx


def _run_opportunistic_sweeps(db: Session) -> None:
    """Board-open sweeps (same pattern as the Breached/Escalated desks): flip stale
    breach flags, then fire time-based automation rules — so what the board shows is
    live truth, not whatever the last cron run left behind. Best-effort."""
    try:
        from app.utils.support_desk.breach import sweep_sla_breach_flags
        changed = sweep_sla_breach_flags(db)
        fired = sweep_time_based_rules(db)
        if changed or fired:
            db.commit()
    except Exception:
        db.rollback()


def _team_rosters(db: Session, teams: list) -> dict:
    """{team_id_str: [user_ids]} — workable (non-collaborator) members incl. lead."""
    out = {}
    for tm in teams:
        roles = tm.member_roles or {}
        ids = [_as_uuid(m) for m in (tm.member_ids or []) if roles.get(str(m)) != "collaborator"]
        ids = [i for i in ids if i]
        lead = _as_uuid(tm.lead_user_id) if tm.lead_user_id else None
        if lead and lead not in ids:
            ids.append(lead)
        out[str(tm.id)] = ids
    return out


def _statuses_of(db: Session, user_ids: set) -> dict:
    """{user_id_str: status} — absent row reads as 'online'."""
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    rows = db.query(SdAgentStatus).filter(SdAgentStatus.user_id.in_(list(ids))).all()
    return {str(r.user_id): r.status for r in rows}


def _rules_by_queue(db: Session) -> dict:
    """{queue_id_str: [rule dicts]} for active on_create rules whose actions route into
    the queue. Rule counts are tiny — Python-side parse (same rationale as the JSONB
    routing matchers in assignment.py)."""
    rules = (db.query(SdAutomationRule)
             .filter(SdAutomationRule.is_deleted == False,  # noqa: E712
                     SdAutomationRule.is_active == True)  # noqa: E712
             .order_by(SdAutomationRule.order_index).all())
    out: dict = {}
    for r in rules:
        for act in (r.actions or []):
            if isinstance(act, dict) and act.get("type") in ("route_queue", "assign_queue"):
                qid = str(act.get("value") or "")
                out.setdefault(qid, []).append({
                    "id": str(r.id), "name": r.name, "trigger": r.trigger,
                    "order_index": r.order_index, "match_type": r.match_type,
                    "conditions": r.conditions or [], "run_count": r.run_count or 0,
                })
    return out


def _grouped_counts(db: Session, queue_ids: list, cond) -> dict:
    """{queue_id_str: count} of non-deleted, unmerged tickets matching ``cond``."""
    if not queue_ids:
        return {}
    rows = (db.query(SdTicket.queue_id, func.count(SdTicket.id))
            .filter(SdTicket.queue_id.in_(queue_ids), SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None), cond)
            .group_by(SdTicket.queue_id).all())
    return {str(r[0]): int(r[1] or 0) for r in rows}


def _my_skill_ids(db: Session, user_id) -> set:
    """Skill ids the caller holds (SdSkill.agent_ids JSONB roster). Small table —
    Python-side membership, same rationale as the routing matchers."""
    rows = (db.query(SdSkill)
            .filter(SdSkill.is_deleted == False, SdSkill.is_active == True).all())  # noqa: E712
    uid = str(user_id)
    return {str(s.id) for s in rows if uid in {str(a) for a in (s.agent_ids or [])}}


def _queue_skill_match(qz: SdQueue, my_skills: set) -> bool:
    """True when the caller holds ALL the queue's required skills (no skills = match)."""
    need = {str(x) for x in (qz.skill_ids or [])}
    return not need or need.issubset(my_skills)


def _my_agent_status(db: Session, user_id) -> str:
    """Caller's availability — absent row reads as 'online' (parity with the roster)."""
    row = db.query(SdAgentStatus).filter(SdAgentStatus.user_id == user_id).first()
    return row.status if row else "online"


def _my_capped_queues(db: Session, queues: list, user_id) -> set:
    """Queue ids where the caller is at/over the per-agent WIP cap (max_agent_load).
    Cap of 0/None = uncapped."""
    cap_queues = [x for x in queues if (x.max_agent_load or 0) > 0]
    if not cap_queues:
        return set()
    rows = (db.query(SdTicket.queue_id, func.count(SdTicket.id))
            .filter(SdTicket.queue_id.in_([x.id for x in cap_queues]),
                    SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None),
                    SdTicket.assigned_agent_id == user_id,
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
            .group_by(SdTicket.queue_id).all())
    load = {str(r[0]): int(r[1] or 0) for r in rows}
    return {str(x.id) for x in cap_queues if load.get(str(x.id), 0) >= int(x.max_agent_load or 0)}


def _health_of(open_n: int, breached: int, due_soon: int, oldest_wait_mins) -> str:
    """Deterministic traffic light: red = meaningful breach load or a ticket rotting
    unassigned past a day; amber = any breach / pressure building; green otherwise."""
    if (breached and open_n and breached / max(open_n, 1) >= 0.25) or \
       (oldest_wait_mins is not None and oldest_wait_mins >= 24 * 60):
        return "red"
    if breached or due_soon >= 3:
        return "amber"
    return "green"


# ═══════════════════════ Skills ═══════════════════════
skills_router = APIRouter(prefix="/support-desk/skills", tags=["Support Desk — Skills"])


def _skill_enrich(db: Session, skills: list) -> list:
    all_agent_ids = set()
    for s in skills:
        all_agent_ids |= {_as_uuid(a) for a in (s.agent_ids or [])}
    all_agent_ids = {a for a in all_agent_ids if a}
    names = {str(r[0]): r[1] for r in db.query(User.id, User.full_name)
             .filter(User.id.in_(list(all_agent_ids))).all()} if all_agent_ids else {}
    queues = db.query(SdQueue).filter(SdQueue.is_deleted == False).all()  # noqa: E712
    q_by_skill: dict = {}
    for q in queues:
        for sid in (q.skill_ids or []):
            q_by_skill[str(sid)] = q_by_skill.get(str(sid), 0) + 1
    for s in skills:
        s.agents = [{"id": str(a), "name": names.get(str(a))}
                    for a in (s.agent_ids or []) if str(a) in names]
        s.queue_count = q_by_skill.get(str(s.id), 0)
    return skills


@skills_router.get("/", response_model=List[SkillResponse])
def list_skills(include_inactive: bool = False, db: Session = Depends(get_db),
                admin: User = Depends(get_support_agent)):
    q = db.query(SdSkill).filter(SdSkill.is_deleted == False)  # noqa: E712
    if not include_inactive:
        q = q.filter(SdSkill.is_active == True)  # noqa: E712
    return _skill_enrich(db, q.order_by(SdSkill.name).all())


@skills_router.post("/", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
def create_skill(payload: SkillCreate, db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    if payload.code and db.query(SdSkill).filter(SdSkill.code == payload.code).first():
        raise HTTPException(400, "Skill code already exists")
    data = payload.model_dump(exclude_unset=True)
    if data.get("agent_ids") is not None:
        data["agent_ids"] = [str(x) for x in data["agent_ids"]]   # JSONB needs JSON-serializable
    s = SdSkill(**data, created_by_id=admin.id)
    db.add(s)
    db.flush()
    write_audit(db, entity_type="skill", op="created", entity_id=s.id, actor_id=admin.id,
                details={"name": s.name})
    db.commit()
    db.refresh(s)
    return _skill_enrich(db, [s])[0]


@skills_router.patch("/{skill_id}", response_model=SkillResponse)
def update_skill(skill_id: UUID, payload: SkillUpdate, db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    s = db.query(SdSkill).filter(SdSkill.id == skill_id, SdSkill.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Skill not found")
    data = payload.model_dump(exclude_unset=True)
    if data.get("code") and db.query(SdSkill).filter(
            SdSkill.code == data["code"], SdSkill.id != skill_id).first():
        # codes are DB-unique — answer with a clean conflict instead of an IntegrityError 500
        raise HTTPException(409, "Skill code already exists")
    for k, v in data.items():
        if k == "agent_ids" and v is not None:
            v = [str(x) for x in v]
        setattr(s, k, v)
    write_audit(db, entity_type="skill", op="updated", entity_id=s.id, actor_id=admin.id,
                details={"name": s.name, "fields": sorted(data.keys())})
    db.commit()
    db.refresh(s)
    return _skill_enrich(db, [s])[0]


@skills_router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill(skill_id: UUID, db: Session = Depends(get_db),
                 admin: User = Depends(get_current_superuser)):
    s = db.query(SdSkill).filter(SdSkill.id == skill_id, SdSkill.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Skill not found")
    holders = [q.name for q in db.query(SdQueue).filter(SdQueue.is_deleted == False).all()  # noqa: E712
               if str(skill_id) in [str(x) for x in (q.skill_ids or [])]]
    if holders:
        raise HTTPException(409, f"Skill is required by queue(s): {', '.join(holders[:5])} — remove it from their routing first.")
    s.is_deleted = True
    s.is_active = False
    if s.code:   # release the unique code (same rule as queue delete)
        s.code = f"{s.code}~{str(s.id)[:8]}"
    write_audit(db, entity_type="skill", op="deleted", entity_id=s.id, actor_id=admin.id,
                details={"name": s.name})
    db.commit()
    return None


# ═══════════════════════ Agent status ═══════════════════════
agent_status_router = APIRouter(prefix="/support-desk", tags=["Support Desk — Agent Status"])


@agent_status_router.get("/agent-status", response_model=AgentStatusRosterResponse)
def agent_status_roster(db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Team-sealed availability roster: my teams' workable members with their status +
    open load. Superusers get every support team's roster."""
    if getattr(admin, "is_superuser", False):
        teams = db.query(SdTeam).filter(SdTeam.is_deleted == False, SdTeam.is_active == True).all()  # noqa: E712
    else:
        from app.routers.support_desk.tickets_self import _team_context
        teams = _team_context(db, admin)["teams"]
    rosters = _team_rosters(db, teams)
    user_team_ids: dict = {}
    all_ids: set = set()
    for tid, ids in rosters.items():
        for uid in ids:
            all_ids.add(uid)
            user_team_ids.setdefault(str(uid), []).append(tid)
    all_ids.add(admin.id)
    rows = db.query(SdAgentStatus).filter(SdAgentStatus.user_id.in_(list(all_ids))).all()
    st = {str(r.user_id): r for r in rows}
    names = {str(r[0]): r[1] for r in db.query(User.id, User.full_name)
             .filter(User.id.in_(list(all_ids))).all()}
    load_rows = (db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
                 .filter(SdTicket.assigned_agent_id.in_(list(all_ids)),
                         SdTicket.is_deleted == False,  # noqa: E712
                         SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                 .group_by(SdTicket.assigned_agent_id).all())
    load = {str(r[0]): int(r[1] or 0) for r in load_rows}

    def entry(uid) -> AgentStatusEntry:
        u = str(uid)
        row = st.get(u)
        return AgentStatusEntry(
            user_id=uid, name=names.get(u),
            status=(row.status if row else "online"),
            status_note=(row.status_note if row else None),
            changed_at=(row.changed_at if row else None),
            open_count=load.get(u, 0),
            team_ids=user_team_ids.get(u, []))

    agents = sorted((entry(uid) for uid in all_ids if str(uid) != str(admin.id)),
                    key=lambda e: (e.name or "").lower())
    return AgentStatusRosterResponse(generated_at=_now(), me=entry(admin.id), agents=agents)


@agent_status_router.put("/me/status", response_model=AgentStatusEntry)
def set_my_status(payload: MyStatusUpdate, db: Session = Depends(get_db),
                  admin: User = Depends(get_support_agent)):
    row = db.query(SdAgentStatus).filter(SdAgentStatus.user_id == admin.id).first()
    if row:
        row.status = payload.status
        row.status_note = payload.status_note
    else:
        row = SdAgentStatus(user_id=admin.id, status=payload.status, status_note=payload.status_note)
        db.add(row)
    db.commit()
    db.refresh(row)
    return AgentStatusEntry(user_id=admin.id, name=getattr(admin, "full_name", None),
                            status=row.status, status_note=row.status_note,
                            changed_at=row.changed_at, open_count=0, team_ids=[])


# ═══════════════════════ Queue overview + stats + tier boards ═══════════════════════
queue_ops_router = APIRouter(prefix="/support-desk/queues", tags=["Support Desk — Queue Engine"])


def _build_cards(db: Session, queues: list, days: int,
                 flow_interval: str = "day") -> list[QueueOverviewCard]:
    now = _now()
    since = now - timedelta(days=days)
    qids = [q.id for q in queues]
    conds = team_ops_conds(now)
    open_set = list(OPEN_TICKET_STATUSES)

    open_c = _grouped_counts(db, qids, SdTicket.status.in_(open_set))
    prog_c = _grouped_counts(db, qids, SdTicket.status == TicketStatus.IN_PROGRESS.value)
    unassigned_c = _grouped_counts(db, qids, and_(SdTicket.status.in_(open_set),
                                                  SdTicket.assigned_agent_id.is_(None)))
    breached_c = _grouped_counts(db, qids, and_(SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)),
                                                conds["breach"]))
    due_soon_c = _grouped_counts(db, qids, conds["due_soon"])
    critical_c = _grouped_counts(db, qids, and_(SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)),
                                                conds["critical"]))
    hold_c = _grouped_counts(db, qids, SdTicket.status == TicketStatus.ON_HOLD.value)
    resolved_c = _grouped_counts(db, qids, and_(SdTicket.resolved_at.isnot(None),
                                                SdTicket.resolved_at >= since))
    # Attainment: of the range's resolved tickets, % that beat their resolution target.
    attained_c = _grouped_counts(db, qids, and_(
        SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= since,
        SdTicket.resolution_due_at.isnot(None), SdTicket.resolved_at <= SdTicket.resolution_due_at))
    with_target_c = _grouped_counts(db, qids, and_(
        SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= since,
        SdTicket.resolution_due_at.isnot(None)))
    # First-response wait (range): avg(first_responded_at - created_at) per queue.
    frt_rows = (db.query(SdTicket.queue_id,
                         func.avg(func.extract("epoch", SdTicket.first_responded_at - SdTicket.created_at)))
                .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                        SdTicket.merged_into_id.is_(None),
                        SdTicket.created_at >= since, SdTicket.first_responded_at.isnot(None))
                .group_by(SdTicket.queue_id).all()) if qids else []
    frt = {str(r[0]): _mins(r[1]) for r in frt_rows}
    # Oldest still-unassigned ticket per queue.
    oldest_rows = (db.query(SdTicket.queue_id, func.min(SdTicket.created_at))
                   .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.status.in_(open_set), SdTicket.assigned_agent_id.is_(None))
                   .group_by(SdTicket.queue_id).all()) if qids else []
    oldest = {}
    for qid, created in oldest_rows:
        c = sla_util._aware(created)
        oldest[str(qid)] = _mins((now - c).total_seconds()) if c else None
    # Flow band (inflow = created, outflow = resolved). Interval-aware: 'hour' (only
    # honoured when days <= 2, capped 48 buckets) or 'day' (capped 14 buckets).
    unit = "hour" if (flow_interval == "hour" and days <= 2) else "day"
    trunc_created = func.date_trunc(unit, SdTicket.created_at)
    trunc_resolved = func.date_trunc(unit, SdTicket.resolved_at)
    inflow_rows = (db.query(SdTicket.queue_id, trunc_created, func.count(SdTicket.id))
                   .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.created_at >= since)
                   .group_by(SdTicket.queue_id, trunc_created).all()) if qids else []
    outflow_rows = (db.query(SdTicket.queue_id, trunc_resolved, func.count(SdTicket.id))
                    .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                            SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= since)
                    .group_by(SdTicket.queue_id, trunc_resolved).all()) if qids else []

    def _bucket_key(dt):
        if dt is None:
            return None
        aware = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        aware = aware.astimezone(timezone.utc)
        if unit == "hour":
            return aware.replace(minute=0, second=0, microsecond=0)
        return aware.date()

    inflow = {(str(r[0]), _bucket_key(r[1])): int(r[2] or 0) for r in inflow_rows}
    outflow = {(str(r[0]), _bucket_key(r[1])): int(r[2] or 0) for r in outflow_rows}
    if unit == "hour":
        n_buckets = min(days * 24, 48)
        b0 = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0) \
            - timedelta(hours=n_buckets - 1)
        bucket_list = [b0 + timedelta(hours=i) for i in range(n_buckets)]
    else:
        n_buckets = min(days, 14)
        day0 = (now - timedelta(days=n_buckets - 1)).date()
        bucket_list = [day0 + timedelta(days=i) for i in range(n_buckets)]

    # ── Vitals Bay telemetry (additive) ──
    # Open-work age histogram per queue: <1h / 1-4h / 4-24h / 1-3d / >3d.
    _open_cond = SdTicket.status.in_(open_set)
    aging_c = {
        "lt_1h": _grouped_counts(db, qids, and_(_open_cond, SdTicket.created_at > now - timedelta(hours=1))),
        "h1_4": _grouped_counts(db, qids, and_(_open_cond,
                                               SdTicket.created_at <= now - timedelta(hours=1),
                                               SdTicket.created_at > now - timedelta(hours=4))),
        "h4_24": _grouped_counts(db, qids, and_(_open_cond,
                                                SdTicket.created_at <= now - timedelta(hours=4),
                                                SdTicket.created_at > now - timedelta(hours=24))),
        "d1_3": _grouped_counts(db, qids, and_(_open_cond,
                                               SdTicket.created_at <= now - timedelta(hours=24),
                                               SdTicket.created_at > now - timedelta(hours=72))),
        "gt_3d": _grouped_counts(db, qids, and_(_open_cond, SdTicket.created_at <= now - timedelta(hours=72))),
    }
    # Burn rate per queue: resolved in the trailing 4h ÷ 4 (same formula as the tier board).
    burn_c = _grouped_counts(db, qids, and_(SdTicket.resolved_at.isnot(None),
                                            SdTicket.resolved_at >= now - timedelta(hours=4)))
    # Reopened events landing in each queue over the range (quality signal).
    reopen_rows = (db.query(SdTicket.queue_id, func.count(SdTicketActivity.id))
                   .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
                   .filter(SdTicketActivity.action == "reopened",
                           SdTicketActivity.created_at >= since,
                           SdTicket.queue_id.in_(qids),
                           SdTicket.is_deleted == False)  # noqa: E712
                   .group_by(SdTicket.queue_id).all()) if qids else []
    reopens_c = {str(r[0]): int(r[1] or 0) for r in reopen_rows}

    # Teams + rosters + presence (one pass).
    team_ids = {q.team_id for q in queues if q.team_id}
    teams = db.query(SdTeam).filter(SdTeam.id.in_(list(team_ids))).all() if team_ids else []
    team_by_id = {str(tm.id): tm for tm in teams}
    rosters = _team_rosters(db, teams)
    all_member_ids = {uid for ids in rosters.values() for uid in ids}
    statuses = _statuses_of(db, all_member_ids)
    rules_map = _rules_by_queue(db)

    cards = []
    for q in queues:
        qid = str(q.id)
        tm = team_by_id.get(str(q.team_id)) if q.team_id else None
        roster = rosters.get(str(q.team_id), []) if q.team_id else []
        online = [u for u in roster if statuses.get(str(u), "online") not in ("away", "offline")]
        flow = [QueueFlowPoint(
            day=(b if unit == "hour" else datetime(b.year, b.month, b.day, tzinfo=timezone.utc)),
            inflow=inflow.get((qid, b), 0), outflow=outflow.get((qid, b), 0),
        ) for b in bucket_list]
        with_target = with_target_c.get(qid, 0)
        attainment = round(100.0 * attained_c.get(qid, 0) / with_target, 1) if with_target else None
        cap = getattr(q, "capacity_limit", None)
        open_n = open_c.get(qid, 0)
        burn_rate = round(burn_c.get(qid, 0) / 4.0, 2)
        drain_eta = round(open_n / burn_rate * 60.0, 1) if burn_rate > 0 and open_n else None
        per_agent_cap = int(getattr(q, "max_agent_load", 0) or 0)
        crew_capacity = (len(roster) * per_agent_cap) if (per_agent_cap and roster) else None
        load_pct = round(100.0 * open_n / crew_capacity, 1) if crew_capacity else None
        card = QueueOverviewCard(
            aging={k: v.get(qid, 0) for k, v in aging_c.items()},
            burn_rate_hr=burn_rate, drain_eta_mins=drain_eta,
            crew_capacity=crew_capacity, load_pct=load_pct,
            reopens_range=reopens_c.get(qid, 0),
            capacity_limit=cap,
            at_capacity=bool(cap and open_c.get(qid, 0) >= int(cap)),
            id=q.id, name=q.name, code=q.code, color=q.color, tier=q.tier,
            is_active=q.is_active, is_default=bool(q.is_default),
            auto_assign=bool(q.auto_assign), assignment_method=q.assignment_method or "round_robin",
            serve_order=q.serve_order or "priority_age", queue_priority=q.queue_priority or 50,
            team_id=q.team_id, team_name=(tm.name if tm else None),
            category_count=len(q.category_ids or []), skill_count=len(q.skill_ids or []),
            rule_count=len(rules_map.get(qid, [])),
            agents_total=len(roster), agents_online=len(online),
            coverage_open=team_on_shift(q.business_hours or (tm.business_hours if tm else None), now),
            open=open_c.get(qid, 0), in_progress=prog_c.get(qid, 0),
            unassigned=unassigned_c.get(qid, 0), breached=breached_c.get(qid, 0),
            due_soon=due_soon_c.get(qid, 0), critical=critical_c.get(qid, 0),
            on_hold=hold_c.get(qid, 0),
            avg_wait_mins=frt.get(qid), oldest_wait_mins=oldest.get(qid),
            sla_attainment_7d=attainment, resolved_7d=resolved_c.get(qid, 0),
            health=_health_of(open_c.get(qid, 0), breached_c.get(qid, 0),
                              due_soon_c.get(qid, 0), oldest.get(qid)),
            flow=flow,
        )
        cards.append(card)
    return cards


@queue_ops_router.get("/overview", response_model=QueuesOverviewResponse)
def queues_overview(
    days: int = Query(7, ge=1, le=90),
    include_inactive: bool = False,
    flow_interval: str = Query("day", pattern="^(day|hour)$"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """The Queue Overview board: one card per visible queue (live counts, wait, SLA
    health, presence), tier rollups, the L1→L2→L3 escalation flow, fleet totals and
    the Vitals Bay telemetry block (deltas / aging / SLA split / burn / utilization /
    breach horizon). Opens with the breach-flag + time-based-rule sweeps so the board
    is live truth. ``flow_interval=hour`` is honoured only when ``days <= 2``."""
    _run_opportunistic_sweeps(db)
    queues, _ctx = _visible_queues(db, admin, include_inactive=include_inactive)
    effective_interval = "hour" if (flow_interval == "hour" and days <= 2) else "day"
    cards = _build_cards(db, queues, days, flow_interval=effective_interval)
    now = _now()
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)
    qids = [q.id for q in queues]

    # Tier rollup.
    tier_rollup: dict = {}
    for c in cards:
        key = str(c.tier) if c.tier else "untiered"
        agg = tier_rollup.setdefault(key, {
            "queues": 0, "open": 0, "unassigned": 0, "breached": 0,
            "agents_online": 0, "agents_total": 0, "resolved_7d": 0})
        agg["queues"] += 1
        agg["open"] += c.open
        agg["unassigned"] += c.unassigned
        agg["breached"] += c.breached
        agg["agents_online"] += c.agents_online
        agg["agents_total"] += c.agents_total
        agg["resolved_7d"] += c.resolved_7d

    # Tier escalation flow (Sankey edges) from tier_moved activities in range.
    tier_by_queue = {str(q.id): q.tier for q in
                     db.query(SdQueue).filter(SdQueue.tier.isnot(None)).all()}
    edges: dict = {}
    if qids:
        # Sealed like everything else: only moves whose ticket now sits in a visible queue.
        moves = (db.query(SdTicketActivity)
                 .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
                 .filter(SdTicketActivity.action == "tier_moved",
                         SdTicketActivity.created_at >= since,
                         SdTicket.queue_id.in_(qids))
                 .order_by(SdTicketActivity.created_at.desc()).limit(2000).all())
        for a in moves:
            d = a.detail or {}
            to_tier = d.get("tier")
            # The activity payload records from_tier at move time — authoritative even if
            # the source queue was later retired or re-tiered; queue lookup is the fallback.
            from_tier = d.get("from_tier") or tier_by_queue.get(str(d.get("from_queue_id") or ""))
            if to_tier and from_tier and int(to_tier) != int(from_tier):
                key = (int(from_tier), int(to_tier))
                edges[key] = edges.get(key, 0) + 1

    # Fleet totals + intake pulse.
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    auto_routed_today = (db.query(func.count(SdTicketActivity.id))
                         .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
                         .filter(SdTicketActivity.action.in_(["routed", "rule_fired"]),
                                 SdTicketActivity.created_at >= day_start,
                                 SdTicket.queue_id.in_(qids)).scalar() or 0) if qids else 0
    skips_today = (db.query(func.count(SdTicketSkip.id))
                   .join(SdTicket, SdTicket.id == SdTicketSkip.ticket_id)
                   .filter(SdTicketSkip.created_at >= day_start,
                           SdTicket.queue_id.in_(qids)).scalar() or 0) if qids else 0
    totals = {
        "open": sum(c.open for c in cards),
        "in_progress": sum(c.in_progress for c in cards),
        "unassigned": sum(c.unassigned for c in cards),
        "breached": sum(c.breached for c in cards),
        "due_soon": sum(c.due_soon for c in cards),
        "critical": sum(c.critical for c in cards),
        "on_hold": sum(c.on_hold for c in cards),
        "resolved_7d": sum(c.resolved_7d for c in cards),
        "agents_online": sum(c.agents_online for c in cards),
        "agents_total": sum(c.agents_total for c in cards),
        "red": sum(1 for c in cards if c.health == "red"),
        "amber": sum(1 for c in cards if c.health == "amber"),
        "green": sum(1 for c in cards if c.health == "green"),
    }
    # ═════════ Vitals Bay telemetry (additive) ═════════
    def _fleet_count(cond) -> int:
        if not qids:
            return 0
        return int(db.query(func.count(SdTicket.id))
                   .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None), cond).scalar() or 0)

    def _reopens_between(t0, t1) -> int:
        if not qids:
            return 0
        return int(db.query(func.count(SdTicketActivity.id))
                   .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
                   .filter(SdTicketActivity.action == "reopened",
                           SdTicketActivity.created_at >= t0, SdTicketActivity.created_at < t1,
                           SdTicket.queue_id.in_(qids),
                           SdTicket.is_deleted == False).scalar() or 0)  # noqa: E712

    def _res_attainment(t0, t1):
        """Resolution attainment % of tickets resolved in [t0, t1) that had a target."""
        with_target = _fleet_count(and_(SdTicket.resolved_at.isnot(None),
                                        SdTicket.resolved_at >= t0, SdTicket.resolved_at < t1,
                                        SdTicket.resolution_due_at.isnot(None)))
        attained = _fleet_count(and_(SdTicket.resolved_at.isnot(None),
                                     SdTicket.resolved_at >= t0, SdTicket.resolved_at < t1,
                                     SdTicket.resolution_due_at.isnot(None),
                                     SdTicket.resolved_at <= SdTicket.resolution_due_at))
        return (round(100.0 * attained / with_target, 1) if with_target else None), with_target

    def _rsp_attainment(t0, t1):
        """Response attainment % of tickets created in [t0, t1) with a response target
        that got their first response inside it (unanswered-and-unbreached excluded)."""
        base = and_(SdTicket.created_at >= t0, SdTicket.created_at < t1,
                    SdTicket.response_due_at.isnot(None))
        answered = _fleet_count(and_(base, SdTicket.first_responded_at.isnot(None)))
        attained = _fleet_count(and_(base, SdTicket.first_responded_at.isnot(None),
                                     SdTicket.first_responded_at <= SdTicket.response_due_at))
        missed_open = _fleet_count(and_(base, SdTicket.first_responded_at.is_(None),
                                        SdTicket.sla_response_breached == True))  # noqa: E712
        denom = answered + missed_open
        return (round(100.0 * attained / denom, 1) if denom else None), denom

    def _delta(now_v, prev_v):
        pct = round(100.0 * (now_v - prev_v) / prev_v, 1) if prev_v else None
        return {"now": now_v, "prev": prev_v, "pct": pct}

    far_future = now + timedelta(days=3650)

    # Period-over-period deltas (event-based metrics only — point-in-time backlog
    # can't be reconstructed without a snapshot store).
    in_now = _fleet_count(and_(SdTicket.created_at >= since, SdTicket.created_at < now))
    in_prev = _fleet_count(and_(SdTicket.created_at >= prev_since, SdTicket.created_at < since))
    out_now = _fleet_count(and_(SdTicket.resolved_at.isnot(None),
                                SdTicket.resolved_at >= since, SdTicket.resolved_at < now))
    out_prev = _fleet_count(and_(SdTicket.resolved_at.isnot(None),
                                 SdTicket.resolved_at >= prev_since, SdTicket.resolved_at < since))
    res_att_now, _ = _res_attainment(since, now)
    res_att_prev, _ = _res_attainment(prev_since, since)
    rsp_att_now, _ = _rsp_attainment(since, now)
    rsp_att_prev, _ = _rsp_attainment(prev_since, since)
    reopens_now = _reopens_between(since, now)
    reopens_prev = _reopens_between(prev_since, since)
    deltas = {
        "inflow": _delta(in_now, in_prev),
        "outflow": _delta(out_now, out_prev),
        "reopens": _delta(reopens_now, reopens_prev),
        "sla_resolution": {"now": res_att_now, "prev": res_att_prev,
                           "pct": (round(res_att_now - res_att_prev, 1)
                                   if res_att_now is not None and res_att_prev is not None else None)},
        "sla_response": {"now": rsp_att_now, "prev": rsp_att_prev,
                         "pct": (round(rsp_att_now - rsp_att_prev, 1)
                                 if rsp_att_now is not None and rsp_att_prev is not None else None)},
    }

    # Fleet aging = sum of the per-card histograms.
    aging_fleet: dict = {"lt_1h": 0, "h1_4": 0, "h4_24": 0, "d1_3": 0, "gt_3d": 0}
    for c in cards:
        for k in aging_fleet:
            aging_fleet[k] += int((c.aging or {}).get(k, 0))

    # SLA split: response vs resolution + per-priority resolution attainment.
    by_priority: dict = {}
    if qids:
        pr_rows = (db.query(SdTicket.priority, func.count(SdTicket.id))
                   .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= since,
                           SdTicket.resolution_due_at.isnot(None))
                   .group_by(SdTicket.priority).all())
        pr_ok_rows = (db.query(SdTicket.priority, func.count(SdTicket.id))
                      .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                              SdTicket.merged_into_id.is_(None),
                              SdTicket.resolved_at.isnot(None), SdTicket.resolved_at >= since,
                              SdTicket.resolution_due_at.isnot(None),
                              SdTicket.resolved_at <= SdTicket.resolution_due_at)
                      .group_by(SdTicket.priority).all())
        pr_total = {r[0]: int(r[1] or 0) for r in pr_rows}
        pr_ok = {r[0]: int(r[1] or 0) for r in pr_ok_rows}
        by_priority = {p: {"resolved": n, "attained": pr_ok.get(p, 0),
                           "pct": round(100.0 * pr_ok.get(p, 0) / n, 1) if n else None}
                       for p, n in pr_total.items()}
    sla_split = {"response": rsp_att_now, "resolution": res_att_now, "by_priority": by_priority}

    # Fleet burn + drain ETA (tier-board formula, fleet-wide).
    fleet_burn_n = _fleet_count(and_(SdTicket.resolved_at.isnot(None),
                                     SdTicket.resolved_at >= now - timedelta(hours=4)))
    fleet_burn = round(fleet_burn_n / 4.0, 2)
    fleet_open = int(totals["open"])
    burn = {"burn_rate_hr": fleet_burn,
            "drain_eta_mins": (round(fleet_open / fleet_burn * 60.0, 1)
                               if fleet_burn > 0 and fleet_open else None)}

    # Crew utilization: fleet load vs capped capacity + the most-loaded agents.
    capped_cards = [c for c in cards if c.crew_capacity]
    open_capped = sum(c.open for c in capped_cards)
    crew_capacity = sum(int(c.crew_capacity or 0) for c in capped_cards)
    top_agents: list = []
    if qids:
        load_rows = (db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
                     .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                             SdTicket.merged_into_id.is_(None),
                             SdTicket.assigned_agent_id.isnot(None),
                             SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                     .group_by(SdTicket.assigned_agent_id)
                     .order_by(func.count(SdTicket.id).desc()).limit(5).all())
        agent_ids = [r[0] for r in load_rows]
        names = ({str(r[0]): r[1] for r in
                  db.query(User.id, User.full_name).filter(User.id.in_(agent_ids)).all()}
                 if agent_ids else {})
        agent_statuses = _statuses_of(db, set(agent_ids))
        # Per-agent WIP cap: the queue fleet's max_agent_load isn't per-agent, so expose
        # the tightest cap among the visible queues as context (None = uncapped).
        caps = [int(q.max_agent_load) for q in queues if (q.max_agent_load or 0) > 0]
        agent_cap = min(caps) if caps else None
        top_agents = [{"user_id": str(r[0]), "name": names.get(str(r[0])) or "Agent",
                       "open_count": int(r[1] or 0),
                       "status": agent_statuses.get(str(r[0]), "online"),
                       "cap": agent_cap} for r in load_rows]
    utilization = {
        "open_capped": open_capped, "crew_capacity": crew_capacity or None,
        "load_pct": (round(100.0 * open_capped / crew_capacity, 1) if crew_capacity else None),
        "top_agents": top_agents,
    }

    # Breach horizon: the next unbreached SLA deadlines across the visible fleet.
    breach_horizon: list = []
    if qids:
        queue_names = {str(q.id): q.name for q in queues}
        cand = (db.query(SdTicket)
                .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                        SdTicket.merged_into_id.is_(None),
                        SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)),
                        SdTicket.sla_paused_since.is_(None),
                        or_(and_(SdTicket.first_responded_at.is_(None),
                                 SdTicket.sla_response_breached == False,  # noqa: E712
                                 SdTicket.response_due_at.isnot(None),
                                 SdTicket.response_due_at > now),
                            and_(SdTicket.resolved_at.is_(None),
                                 SdTicket.sla_resolution_breached == False,  # noqa: E712
                                 SdTicket.resolution_due_at.isnot(None),
                                 SdTicket.resolution_due_at > now)))
                .order_by(func.least(func.coalesce(SdTicket.response_due_at, far_future),
                                     func.coalesce(SdTicket.resolution_due_at, far_future)).asc())
                .limit(200).all())
        scored = []
        for t in cand:
            options = []
            rsp = sla_util._aware(t.response_due_at)
            res = sla_util._aware(t.resolution_due_at)
            if t.first_responded_at is None and not t.sla_response_breached and rsp and rsp > now:
                options.append(("response", rsp))
            if t.resolved_at is None and not t.sla_resolution_breached and res and res > now:
                options.append(("resolution", res))
            if not options:
                continue
            kind, due = min(options, key=lambda o: o[1] or far_future)
            scored.append((due, kind, t))
        scored.sort(key=lambda s: s[0])
        breach_horizon = [{
            "id": str(t.id), "ticket_number": t.ticket_number, "subject": t.subject,
            "priority": t.priority, "queue_id": str(t.queue_id),
            "queue_name": queue_names.get(str(t.queue_id)),
            "kind": kind, "due_at": due.isoformat(),
            "due_in_seconds": max(0, int((due - now).total_seconds())),
        } for due, kind, t in scored[:8]]

    return QueuesOverviewResponse(
        generated_at=now, queue_count=len(cards), queues=cards,
        tier_rollup=tier_rollup,
        tier_flow=[TierFlowEdge(from_tier=k[0], to_tier=k[1], count=v)
                   for k, v in sorted(edges.items())],
        totals=totals, auto_routed_today=int(auto_routed_today), skips_today=int(skips_today),
        flow_interval=effective_interval, deltas=deltas, aging=aging_fleet,
        sla_split=sla_split, burn=burn, utilization=utilization,
        breach_horizon=breach_horizon, reopens_range=reopens_now)


def _require_visible_queue(db: Session, admin: User, queue_id) -> SdQueue:
    """404 (not 403) outside the seal so queue existence doesn't leak across teams."""
    queues, _ctx = _visible_queues(db, admin, include_inactive=True)
    q = next((x for x in queues if str(x.id) == str(queue_id)), None)
    if not q:
        raise HTTPException(404, "Queue not found")
    return q


@queue_ops_router.get("/tier/{tier}/board", response_model=TierBoardResponse)
def tier_board(
    tier: int,
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    queue_id: Optional[UUID] = None,
    unassigned_only: bool = False,
    mine: bool = False,
    escalated_only: bool = False,
    q: Optional[str] = None,
    sort_by: str = Query("serve", description="serve|created_at|sla|priority|updated_at"),
    sort_dir: str = Query("asc"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """One tier's working queue: the tickets across every visible queue at this tier +
    the tier stats block, in one request. Team-sealed like every other desk surface."""
    if tier not in (1, 2, 3):
        raise HTTPException(422, "tier must be 1, 2 or 3")
    _run_opportunistic_sweeps(db)
    queues, _ctx = _visible_queues(db, admin, tier=tier)
    now = _now()
    if not queues:
        # ⚠ Hand-built twin of the populated stats dict below — a new stats key MUST be
        # added here too or the empty-tier response drifts from the populated one.
        day_start0 = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        return TierBoardResponse(tier=tier, generated_at=now, items=[], total=0, queues=[],
                                 stats={"status_counts": {}, "priority_counts": {}, "unassigned": 0,
                                        "breached": 0, "due_soon": 0, "escalated": 0, "my_load": 0,
                                        "skips_today": 0, "oldest_wait_mins": None, "no_queues": True,
                                        "my_status": _my_agent_status(db, admin.id),
                                        "next_breach_at": None, "burn_rate_hr": 0.0,
                                        "drain_eta_mins": None, "resolved_today": 0,
                                        "my_resolved_today": 0, "my_breach_saves_today": 0,
                                        "health": "green",
                                        # L2 workbench telemetry (desk-wide "my logged" is
                                        # real even with no visible queues on this tier)
                                        "ack_pending": 0, "swarm_active": 0, "watching": 0,
                                        "my_logged_today_mins": _my_logged_today(db, admin.id, day_start0),
                                        # L3 workbench telemetry (problems are desk-wide, so
                                        # they're real even with no visible queues on this tier)
                                        "mi_active": 0, "missing_rca": 0, "fix_in_progress": 0,
                                        "problems_open": _problems_open(db),
                                        "known_errors": _known_errors(db)})
    qids = [x.id for x in queues]
    base = (db.query(SdTicket)
            .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None)))
    query = base
    if status_f:
        query = query.filter(SdTicket.status == status_f)
    else:
        query = query.filter(SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if queue_id:
        if str(queue_id) not in {str(x) for x in qids}:
            raise HTTPException(404, "Queue not found")
        query = query.filter(SdTicket.queue_id == queue_id)
    if unassigned_only:
        query = query.filter(SdTicket.assigned_agent_id.is_(None))
    if mine:
        query = query.filter(SdTicket.assigned_agent_id == admin.id)
    if escalated_only:
        query = query.filter(SdTicket.is_escalated == True)  # noqa: E712
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like),
                                 SdTicket.ticket_number.ilike(like),
                                 SdTicket.contact_name.ilike(like)))
    total = query.count()

    # Priority rank as a portable CASE (array_position would need a PG array literal).
    from sqlalchemy import case as sa_case
    pri_case = sa_case(
        {p: i for i, p in enumerate(PRIORITY_ORDER)}, value=SdTicket.priority, else_=-1)
    if sort_by == "sla":
        order = [SdTicket.resolution_due_at.is_(None), SdTicket.resolution_due_at.asc()]
        rows = query.order_by(*order).offset((page - 1) * limit).limit(limit).all()
    elif sort_by == "priority":
        rows = (query.order_by(pri_case.desc(), SdTicket.created_at.asc())
                .offset((page - 1) * limit).limit(limit).all())
    elif sort_by == "updated_at":
        rows = query.order_by(SdTicket.updated_at.desc()).offset((page - 1) * limit).limit(limit).all()
    elif sort_by == "created_at":
        order = [SdTicket.created_at.asc() if sort_dir == "asc" else SdTicket.created_at.desc()]
        rows = query.order_by(*order).offset((page - 1) * limit).limit(limit).all()
    else:
        # 'serve' — the play-mode order: queue drain priority (a QUEUE column, so it
        # must sort in Python), then urgency, then age. Sorted over a bounded working
        # set BEFORE pagination so page 2 continues the same global order.
        qpri = {str(x.id): (x.queue_priority or 50) for x in queues}
        pri_rank = {p: i for i, p in enumerate(PRIORITY_ORDER)}
        working = query.order_by(SdTicket.created_at.asc()).limit(400).all()
        working.sort(key=lambda t: (
            -(qpri.get(str(t.queue_id), 50)),
            -(pri_rank.get(t.priority, -1)),
            sla_util._aware(t.created_at).timestamp() if t.created_at else 0.0))
        rows = working[(page - 1) * limit: (page - 1) * limit + limit]

    from app.routers.support_desk._common import enrich_tickets
    items = [TicketResponse.model_validate(t).model_dump(mode="json")
             for t in enrich_tickets(db, rows)]

    # Ghost livery: who has each of THIS PAGE's tickets open right now (fresh presence,
    # one bounded query) — explains on the board why serve-next slipstreams past a row.
    if rows:
        fresh_cut = now - timedelta(seconds=_VIEWER_FRESH_SECONDS)
        viewer_rows = (db.query(SdTicketViewer.ticket_id, User.full_name)
                       .join(User, User.id == SdTicketViewer.user_id)
                       .filter(SdTicketViewer.ticket_id.in_([t.id for t in rows]),
                               SdTicketViewer.user_id != admin.id,
                               SdTicketViewer.last_seen_at >= fresh_cut).all())
        viewing: dict = {}
        for tid, vname in viewer_rows:
            viewing.setdefault(str(tid), []).append(vname or "Agent")
        for it in items:
            it["viewing"] = viewing.get(str(it.get("id")), [])
        # L2 workbench badges for this page (two bounded queries, additive keys).
        page_ids = [t.id for t in rows]
        swarming_ids = {str(sid) for (sid,) in
                        db.query(SdSwarmSession.ticket_id)
                        .filter(SdSwarmSession.ticket_id.in_(page_ids),
                                SdSwarmSession.status == "active").all()}
        watching_ids = {str(wid) for (wid,) in
                        db.query(SdTicketWatcher.ticket_id)
                        .filter(SdTicketWatcher.ticket_id.in_(page_ids),
                                SdTicketWatcher.user_id == admin.id).all()}
        for it in items:
            it["swarming"] = str(it.get("id")) in swarming_ids
            it["watching"] = str(it.get("id")) in watching_ids

    # Stats block (whole tier, independent of the current filters).
    conds = team_ops_conds(now)
    open_set = list(OPEN_TICKET_STATUSES)
    active = base.filter(SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
    status_rows = (db.query(SdTicket.status, func.count(SdTicket.id))
                   .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                   .group_by(SdTicket.status).all())
    pri_rows = (db.query(SdTicket.priority, func.count(SdTicket.id))
                .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                        SdTicket.merged_into_id.is_(None),
                        SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                .group_by(SdTicket.priority).all())
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    oldest_created = (db.query(func.min(SdTicket.created_at))
                      .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                              SdTicket.merged_into_id.is_(None),
                              SdTicket.status.in_(open_set),
                              SdTicket.assigned_agent_id.is_(None)).scalar())
    oc = sla_util._aware(oldest_created)
    per_queue_open = _grouped_counts(db, qids, SdTicket.status.in_(open_set))
    per_queue_mine = _grouped_counts(db, qids, and_(
        SdTicket.assigned_agent_id == admin.id,
        SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES))))

    # ── pit-wall telemetry (additive; dict-typed schema keeps this contract-safe) ──
    breached_n = active.filter(conds["breach"]).count()
    due_soon_n = active.filter(conds["due_soon"]).count()
    open_active_n = active.filter(SdTicket.status.in_(open_set)).count()
    workable_n = active.filter(SdTicket.status.in_(_SERVE_STATUSES)).count()
    # Soonest future resolution deadline among actively-workable tickets → breach ticker.
    next_due = (db.query(func.min(SdTicket.resolution_due_at))
                .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                        SdTicket.merged_into_id.is_(None),
                        SdTicket.status.in_(_SERVE_STATUSES),
                        SdTicket.resolution_due_at.isnot(None),
                        SdTicket.resolution_due_at > now).scalar())
    # Burn rate over the trailing 4h → drain ETA ("laps remaining").
    resolved_4h = (db.query(func.count(SdTicket.id))
                   .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.resolved_at.isnot(None),
                           SdTicket.resolved_at >= now - timedelta(hours=4)).scalar() or 0)
    burn_rate_hr = round(int(resolved_4h) / 4.0, 2)
    drain_eta_mins = round(workable_n / burn_rate_hr * 60.0, 1) if burn_rate_hr > 0 and workable_n else None
    resolved_today_q = (db.query(func.count(SdTicket.id))
                        .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                                SdTicket.merged_into_id.is_(None),
                                SdTicket.resolved_at.isnot(None),
                                SdTicket.resolved_at >= day_start))
    my_skills = _my_skill_ids(db, admin.id)
    oldest_wait = _mins((now - oc).total_seconds()) if oc else None

    stats = {
        "status_counts": {r[0]: int(r[1] or 0) for r in status_rows},
        "priority_counts": {r[0]: int(r[1] or 0) for r in pri_rows},
        "unassigned": active.filter(SdTicket.status.in_(open_set),
                                    SdTicket.assigned_agent_id.is_(None)).count(),
        "breached": breached_n,
        "due_soon": due_soon_n,
        "escalated": active.filter(SdTicket.is_escalated == True).count(),  # noqa: E712
        "my_load": active.filter(SdTicket.assigned_agent_id == admin.id).count(),
        "skips_today": int(db.query(func.count(SdTicketSkip.id))
                           .filter(SdTicketSkip.user_id == admin.id,
                                   SdTicketSkip.created_at >= day_start).scalar() or 0),
        "oldest_wait_mins": oldest_wait,
        # pit-wall telemetry
        "my_status": _my_agent_status(db, admin.id),
        "next_breach_at": next_due.isoformat() if next_due else None,
        "burn_rate_hr": burn_rate_hr,
        "drain_eta_mins": drain_eta_mins,
        "resolved_today": int(resolved_today_q.scalar() or 0),
        "my_resolved_today": int(resolved_today_q.filter(SdTicket.resolved_by_id == admin.id).scalar() or 0),
        # breach saves: my resolves today that beat their resolution deadline
        "my_breach_saves_today": int(resolved_today_q.filter(
            SdTicket.resolved_by_id == admin.id,
            SdTicket.resolution_due_at.isnot(None),
            SdTicket.resolved_at <= SdTicket.resolution_due_at).scalar() or 0),
        "health": _health_of(open_active_n, breached_n, due_soon_n, oldest_wait),
        # ── L2 workbench telemetry (additive; mirror any new key in the no_queues dict above) ──
        # Escalated work nobody has acknowledged yet — the ACK clock.
        "ack_pending": active.filter(SdTicket.is_escalated == True,  # noqa: E712
                                     SdTicket.escalation_acknowledged_at.is_(None)).count(),
        "swarm_active": int(db.query(func.count(SdSwarmSession.id))
                            .join(SdTicket, SdTicket.id == SdSwarmSession.ticket_id)
                            .filter(SdSwarmSession.status == "active",
                                    SdTicket.queue_id.in_(qids),
                                    SdTicket.is_deleted == False,  # noqa: E712
                                    SdTicket.merged_into_id.is_(None)).scalar() or 0),
        # Tickets on THIS tier the caller follows.
        "watching": int(db.query(func.count(SdTicketWatcher.id))
                        .join(SdTicket, SdTicket.id == SdTicketWatcher.ticket_id)
                        .filter(SdTicketWatcher.user_id == admin.id,
                                SdTicket.queue_id.in_(qids),
                                SdTicket.is_deleted == False,  # noqa: E712
                                SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES))).scalar() or 0),
        # Desk-wide (the caller's timesheet doesn't stop at a tier boundary).
        "my_logged_today_mins": _my_logged_today(db, admin.id, day_start),
        # ── L3 workbench telemetry (additive; mirror any new key in the no_queues dict above) ──
        # Live major incidents on this tier.
        "mi_active": active.filter(SdTicket.is_major_incident == True).count(),  # noqa: E712
        # Breached tier tickets with NO root-cause record at all — the RCA debt
        # (v2 single truth: returned/stale filings correctly read as missing).
        "missing_rca": active.filter(conds["breach"], _rca_missing()).count(),
        # Tier tickets whose linked change request is actually moving (approved →
        # implemented) — the "permanent fix in flight" lens.
        "fix_in_progress": int(db.query(func.count(SdTicket.id))
                               .join(SdChangeRequest, SdChangeRequest.id == SdTicket.linked_change_id)
                               .filter(SdTicket.queue_id.in_(qids),
                                       SdTicket.is_deleted == False,  # noqa: E712
                                       SdTicket.merged_into_id.is_(None),
                                       SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)),
                                       SdChangeRequest.is_deleted == False,  # noqa: E712
                                       SdChangeRequest.status.in_(["approved", "scheduled", "implemented"]))
                               .scalar() or 0),
        # Desk-wide problem-management telemetry (problems aren't tier-scoped).
        "problems_open": _problems_open(db),
        "known_errors": _known_errors(db),
    }
    # Setup sheet: resolve the tier's referenced skill ids to names once.
    ref_skill_ids = {str(s) for x in queues for s in (x.skill_ids or [])}
    skill_names = {}
    if ref_skill_ids:
        skill_names = {str(s.id): s.name for s in
                       db.query(SdSkill).filter(SdSkill.id.in_([_as_uuid(i) for i in ref_skill_ids if _as_uuid(i)])).all()}

    return TierBoardResponse(
        tier=tier, generated_at=now, items=items, total=total,
        queues=[{"id": str(x.id), "name": x.name, "color": x.color,
                 "serve_order": x.serve_order or "priority_age",
                 "queue_priority": x.queue_priority or 50,
                 "open": per_queue_open.get(str(x.id), 0),
                 "my_active": per_queue_mine.get(str(x.id), 0),
                 "max_agent_load": x.max_agent_load or 0,
                 "skill_match": _queue_skill_match(x, my_skills),
                 "skills": [{"id": str(s), "name": skill_names.get(str(s), "Skill"),
                             "mine": str(s) in my_skills} for s in (x.skill_ids or [])]} for x in queues],
        stats=stats)


@queue_ops_router.post("/tier/{tier}/serve-next", response_model=ServeNextResponse)
def serve_next(
    tier: int,
    request: Request,
    queue_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Play mode (Zendesk guided mode / ServiceNow inbox): claim the next unowned
    ticket in this tier per each queue's serve_order, draining higher queue_priority
    queues first. Skips tickets another agent is viewing right now (presence-based
    collision avoidance) and tickets the caller skipped today."""
    if tier not in (1, 2, 3):
        raise HTTPException(422, "tier must be 1, 2 or 3")
    # Availability gate (ServiceNow AWA parity): an away/offline agent can't be dealt
    # work — guided serve respects the same presence signal auto-assignment does.
    my_status = _my_agent_status(db, admin.id)
    if my_status in ("away", "offline"):
        raise HTTPException(409, f"You're set '{my_status}' — switch your status to Online to serve the queue.")
    queues, _ctx = _visible_queues(db, admin, tier=tier)
    if queue_id:
        queues = [x for x in queues if str(x.id) == str(queue_id)]
    if not queues:
        return ServeNextResponse(ticket=None, remaining=0, reason="no_queues")
    now = _now()
    qids = [x.id for x in queues]
    pool = (db.query(SdTicket)
            .filter(SdTicket.queue_id.in_(qids), SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None),
                    SdTicket.status.in_(_SERVE_STATUSES),
                    SdTicket.assigned_agent_id.is_(None))
            .order_by(SdTicket.created_at.asc())
            .limit(400).all())
    if not pool:
        return ServeNextResponse(ticket=None, remaining=0, reason="drained")

    # Exclusions: fresh viewers (someone else has it open) + my skips today.
    fresh = now - timedelta(seconds=_VIEWER_FRESH_SECONDS)
    viewed = {str(r[0]) for r in (db.query(SdTicketViewer.ticket_id)
              .filter(SdTicketViewer.ticket_id.in_([t.id for t in pool]),
                      SdTicketViewer.user_id != admin.id,
                      SdTicketViewer.last_seen_at >= fresh).all())}
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    skipped = {str(r[0]) for r in (db.query(SdTicketSkip.ticket_id)
               .filter(SdTicketSkip.user_id == admin.id,
                       SdTicketSkip.created_at >= day_start).all())}
    candidates = [t for t in pool if str(t.id) not in viewed and str(t.id) not in skipped]
    if not candidates:
        return ServeNextResponse(ticket=None, remaining=len(pool),
                                 reason="all_viewed" if viewed else "drained")

    # WIP-cap gate: queues where the caller already sits at max_agent_load are
    # excluded from guided serve (the cap finally binds outside auto-assign too).
    capped = _my_capped_queues(db, queues, admin.id)
    if capped:
        uncapped = [t for t in candidates if str(t.queue_id) not in capped]
        if not uncapped:
            return ServeNextResponse(ticket=None, remaining=len(candidates), reason="at_capacity")
        candidates = uncapped

    q_by_id = {str(x.id): x for x in queues}
    pri_rank = {p: i for i, p in enumerate(PRIORITY_ORDER)}
    my_skills = _my_skill_ids(db, admin.id)

    def rank(t: SdTicket):
        qz = q_by_id.get(str(t.queue_id))
        # Skill-fit first (Zendesk skills-based routing parity): matched lanes serve
        # before mismatched ones, but mismatches stay reachable — fail-open, never starve.
        fit = 0 if (qz is None or _queue_skill_match(qz, my_skills)) else 1
        qpri = -(qz.queue_priority or 50) if qz else 0
        if qz and (qz.serve_order or "priority_age") == "sla_breach":
            due = sla_util._aware(t.resolution_due_at)
            inner = (0, due.timestamp() if due else float("inf"))
        else:
            created = sla_util._aware(t.created_at)
            inner = (-(pri_rank.get(t.priority, -1)), created.timestamp() if created else 0.0)
        return (fit, qpri, *inner)

    candidates.sort(key=rank)
    t = candidates[0]
    if t.assigned_agent_id is not None:   # defensive re-check (serialized get_db, but be safe)
        raise HTTPException(409, "That ticket was just claimed — try again.")
    t.assigned_agent_id = admin.id
    if t.status == TicketStatus.OPEN.value:
        # Serving starts the work: OPEN → IN_PROGRESS with pause bookkeeping parity.
        sla_util.apply_pause_transition(t, TicketStatus.OPEN.value, TicketStatus.IN_PROGRESS.value, now)
        t.status = TicketStatus.IN_PROGRESS.value
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=admin.id,
        actor_name=getattr(admin, "full_name", None) or "Agent",
        action="assigned",
        detail={"assigned_agent_id": str(admin.id), "by": "serve-next", "tier": tier}))
    write_audit(db, entity_type="ticket", op="assigned", entity_id=t.id, actor_id=admin.id,
                request=request, details={"by": "serve-next", "tier": tier})
    db.commit()
    db.refresh(t)
    from app.routers.support_desk._common import enrich_ticket
    return ServeNextResponse(
        ticket=TicketResponse.model_validate(enrich_ticket(db, t)).model_dump(mode="json"),
        remaining=max(0, len(candidates) - 1), reason=None)


@queue_ops_router.post("/route-unrouted", response_model=dict)
def route_unrouted_backlog(
    request: Request,
    dry_run: bool = False,
    cap: int = Query(1000, ge=1, le=5000),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """One-shot backfill: run the create-time routing chain over every live,
    non-terminal ticket that has NO queue, so a backlog that predates the queue
    engine (or a newly-laid lane) lands on the tier boards.

    ROUTE-ONLY, by design: stamps ``queue_id`` (+ ``team_id`` when empty) and
    nothing else — no auto-assignment, no priority/SLA/tag rewrites. Reshuffling
    ownership or restarting clocks on aged tickets is exactly what a backfill
    must never do. Chain per ticket (create parity, plus one backfill extra):

      1. routing rules  — ``evaluate_rules`` in dry-run (decision only, run
         counters untouched); only the routing half of the decision is applied
      2. category map   — the queue whose ``category_ids`` owns the category
      3. team lane      — backfill extra: the ticket already carries a team →
         that team's highest-drain lane, so the team seal stays intact
      4. default queue  — ``is_default``; only when queue AND team are both
         empty, mirroring ``apply_default_queue``'s create-time semantics

    ``dry_run=true`` returns the same report without writing anything.
    Idempotent: only ``queue_id IS NULL`` tickets are candidates, so a re-run
    skips everything a previous run placed.
    """
    from uuid import uuid4
    from app.utils.support_desk.rules import evaluate_rules
    from app.utils.support_desk.assignment import _find_queue_for_category

    candidates = (db.query(SdTicket)
                  .filter(SdTicket.is_deleted == False,  # noqa: E712
                          SdTicket.merged_into_id.is_(None),
                          SdTicket.queue_id.is_(None),
                          SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                  .order_by(SdTicket.created_at)
                  .limit(cap + 1).all())
    remaining = max(0, len(candidates) - cap)
    candidates = candidates[:cap]

    active_queues = (db.query(SdQueue)
                     .filter(SdQueue.is_deleted == False, SdQueue.is_active == True)  # noqa: E712
                     .order_by(SdQueue.queue_priority.desc(), SdQueue.name).all())
    queues_by_id = {str(q.id): q for q in active_queues}
    default_q = next((q for q in active_queues if q.is_default), None)
    team_lane = {}
    for qz in active_queues:
        if qz.team_id and str(qz.team_id) not in team_lane:
            team_lane[str(qz.team_id)] = qz   # first hit = that team's highest-drain lane

    via = {"rule": 0, "category": 0, "team_lane": 0, "default_queue": 0}
    unrouted, routed, overflowed = [], 0, 0
    cand_team_ids = {t.team_id for t in candidates if t.team_id}
    team_names = ({str(r.id): r.name for r in
                   db.query(SdTeam).filter(SdTeam.id.in_(cand_team_ids)).all()}
                  if cand_team_ids else {})
    from app.utils.support_desk.assignment import apply_overflow
    for t in candidates:
        queue, how, rule_via = None, None, None
        decision = evaluate_rules(db, t, trigger="on_create", dry_run=True).get("decision", {})
        if decision.get("queue_id"):
            queue = queues_by_id.get(str(decision["queue_id"]))
            how, rule_via = "rule", decision.get("via")
        if queue is None:
            queue = _find_queue_for_category(db, t.category_id, t.subcategory_id)
            if queue is not None:
                how = "category"
        if queue is None and t.team_id:
            queue = team_lane.get(str(t.team_id))
            if queue is not None:
                how = "team_lane"
        if queue is None and t.team_id is None and default_q is not None:
            queue, how = default_q, "default_queue"
        if queue is not None:
            queue, hopped = apply_overflow(db, queue)   # capacity spill (create parity)
            if hopped:
                overflowed += 1

        if queue is None:
            if len(unrouted) < 50:
                # reason_code is the machine key the Sweep modal's FIX actions key off
                if t.team_id:
                    code, reason = "team_no_lane", "team has no lane"
                elif default_q is None:
                    code, reason = "no_default", "no default lane"
                else:
                    code, reason = "no_match", "no route matched"
                unrouted.append({"id": str(t.id), "ticket_number": t.ticket_number,
                                 "subject": t.subject, "reason": reason, "reason_code": code,
                                 "team_id": str(t.team_id) if t.team_id else None,
                                 "team_name": team_names.get(str(t.team_id)) if t.team_id else None})
            continue

        routed += 1
        via[how] += 1
        if not dry_run:
            t.queue_id = queue.id
            if queue.team_id and not t.team_id:
                t.team_id = queue.team_id
            db.add(SdTicketActivity(
                ticket_id=t.id, actor_user_id=admin.id, actor_name="Routing",
                action="routed",
                detail={"queue": queue.name, "queue_id": str(queue.id),
                        "by": "backfill", "via": rule_via or how}))

    if not dry_run and routed:
        # Desk-wide governance op with no single entity — a synthetic run-id keys
        # the audit row; per-ticket provenance lives in the 'routed' activities.
        write_audit(db, entity_type="queue", op="route_backfill", entity_id=uuid4(),
                    actor_id=admin.id, request=request,
                    details={"scanned": len(candidates), "routed": routed, "via": via,
                             "overflowed": overflowed,
                             "unrouted": len(candidates) - routed, "remaining": remaining})
        db.commit()
    return {"dry_run": dry_run, "scanned": len(candidates), "routed": routed, "via": via,
            "overflowed": overflowed,
            "unrouted_count": len(candidates) - routed, "unrouted": unrouted,
            "remaining": remaining}


# NOTE: literal path — registered before ``/{queue_id}/stats`` in file order for
# clarity (single- vs two-segment paths can't actually collide, but keep the habit).
@queue_ops_router.get("/config-ledger", response_model=ConfigLedgerResponse)
def config_ledger(
    page: int = Query(1, ge=1),
    limit: int = Query(30, ge=1, le=100),
    entity: Optional[str] = Query(None, description="queue|rule|skill|sla_package|setting"),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """The Queue Config change ledger: every audited config mutation across queues,
    routing rules, skills, SLA packages and desk settings, newest first. Rule rows
    link onward to ``/automation-rules/{id}/revisions`` for the version history."""
    import json as _json
    from app.models.audit_log import AuditLog

    kinds = ["queue", "rule", "skill", "sla_package", "setting"]
    if entity:
        if entity not in kinds:
            raise HTTPException(422, f"entity must be one of {kinds}")
        kinds = [entity]
    types = [f"support.{k}" for k in kinds]
    base = db.query(AuditLog).filter(AuditLog.entity_type.in_(types))
    total = base.count()
    rows = (base.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * limit).limit(limit).all())
    names = {}
    ids = {r.user_id for r in rows if r.user_id}
    if ids:
        names = {str(u.id): u.full_name for u in db.query(User).filter(User.id.in_(ids)).all()}
    items = []
    for r in rows:
        try:
            details = _json.loads(r.details) if r.details else {}
            if not isinstance(details, dict):
                details = {"raw": details}
        except Exception:
            details = {"raw": r.details}
        items.append(ConfigLedgerEntry(
            id=r.id, action=r.action, entity_type=r.entity_type, entity_id=r.entity_id,
            actor_id=r.user_id, actor_name=names.get(str(r.user_id)) if r.user_id else None,
            details=details, created_at=r.created_at))
    return ConfigLedgerResponse(total=total, page=page, limit=limit, items=items)


@queue_ops_router.get("/{queue_id}/stats", response_model=QueueStatsResponse)
def queue_stats(queue_id: UUID, days: int = Query(7, ge=1, le=90),
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Queue drawer drill: the overview card + status/priority mix, roster load,
    routing surface (categories, skills, rules in) and recent activity."""
    qrow = _require_visible_queue(db, admin, queue_id)
    now = _now()
    card = _build_cards(db, [qrow], days)[0]
    base = (db.query(SdTicket)
            .filter(SdTicket.queue_id == qrow.id, SdTicket.is_deleted == False,  # noqa: E712
                    SdTicket.merged_into_id.is_(None),
                    SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES))))
    status_rows = (db.query(SdTicket.status, func.count(SdTicket.id))
                   .filter(SdTicket.queue_id == qrow.id, SdTicket.is_deleted == False,  # noqa: E712
                           SdTicket.merged_into_id.is_(None),
                           SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                   .group_by(SdTicket.status).all())
    pri_rows = (db.query(SdTicket.priority, func.count(SdTicket.id))
                .filter(SdTicket.queue_id == qrow.id, SdTicket.is_deleted == False,  # noqa: E712
                        SdTicket.merged_into_id.is_(None),
                        SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                .group_by(SdTicket.priority).all())

    # Roster load.
    load = []
    if qrow.team_id:
        tm = db.query(SdTeam).filter(SdTeam.id == qrow.team_id).first()
        roster = _team_rosters(db, [tm]).get(str(tm.id), []) if tm else []
        statuses = _statuses_of(db, set(roster))
        names = {str(r[0]): r[1] for r in db.query(User.id, User.full_name)
                 .filter(User.id.in_(roster)).all()} if roster else {}
        load_rows = (db.query(SdTicket.assigned_agent_id, func.count(SdTicket.id))
                     .filter(SdTicket.assigned_agent_id.in_(roster),
                             SdTicket.queue_id == qrow.id,
                             SdTicket.is_deleted == False,  # noqa: E712
                             SdTicket.status.notin_(list(TERMINAL_TICKET_STATUSES)))
                     .group_by(SdTicket.assigned_agent_id).all()) if roster else []
        counts = {str(r[0]): int(r[1] or 0) for r in load_rows}
        load = [{"user_id": str(u), "name": names.get(str(u)),
                 "status": statuses.get(str(u), "online"),
                 "open_count": counts.get(str(u), 0)} for u in roster]
        load.sort(key=lambda e: -e["open_count"])

    cat_ids = [_as_uuid(c) for c in (qrow.category_ids or [])]
    cat_ids = [c for c in cat_ids if c]
    categories = [{"id": str(r.id), "name": r.name} for r in
                  db.query(SdCategory).filter(SdCategory.id.in_(cat_ids)).all()] if cat_ids else []
    skill_ids = [_as_uuid(s) for s in (qrow.skill_ids or [])]
    skill_ids = [s for s in skill_ids if s]
    skills = [{"id": str(r.id), "name": r.name, "color": r.color,
               "agent_count": len(r.agent_ids or [])} for r in
              db.query(SdSkill).filter(SdSkill.id.in_(skill_ids),
                                       SdSkill.is_deleted == False).all()] if skill_ids else []  # noqa: E712
    rules = _rules_by_queue(db).get(str(qrow.id), [])

    acts = (db.query(SdTicketActivity, SdTicket.ticket_number)
            .join(SdTicket, SdTicket.id == SdTicketActivity.ticket_id)
            .filter(SdTicket.queue_id == qrow.id)
            .order_by(SdTicketActivity.created_at.desc()).limit(15).all())
    recent = [{"ticket_number": num, "ticket_id": str(a.ticket_id), "action": a.action,
               "actor": a.actor_name, "at": a.created_at.isoformat() if a.created_at else None,
               "detail": a.detail or {}} for a, num in acts]

    return QueueStatsResponse(
        id=qrow.id, name=qrow.name, generated_at=now, card=card,
        status_counts={r[0]: int(r[1] or 0) for r in status_rows},
        priority_counts={r[0]: int(r[1] or 0) for r in pri_rows},
        load=load, categories=categories, skills=skills, rules=rules,
        recent_activity=recent)


# ═══════════════════════ Ticket tier actions (skip / escalate / descend) ═══════════════════════
ticket_tier_router = APIRouter(prefix="/support-desk/tickets", tags=["Support Desk — Tier Actions"])


def _queue_tier_of(db: Session, t: SdTicket) -> Optional[int]:
    if not t.queue_id:
        return None
    q = db.query(SdQueue).filter(SdQueue.id == t.queue_id).first()
    return q.tier if q else None


@ticket_tier_router.post("/{ticket_id}/skip", response_model=TicketResponse)
def skip_ticket(ticket_id: UUID, payload: SkipCreate, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Play-mode skip (reason REQUIRED — Zendesk skip governance). The ticket returns
    to the pool: if serve-next just parked it on the caller, it is un-assigned. The
    skip is excluded from the caller's serve rotation for the rest of the day and
    lands in the supervisor skip report."""
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    if payload.reason_code not in SKIP_REASON_CODES:
        raise HTTPException(422, f"reason_code must be one of {SKIP_REASON_CODES}")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "This ticket is already resolved/closed — nothing to skip.")
    if t.assigned_agent_id and str(t.assigned_agent_id) != str(admin.id):
        raise HTTPException(409, "This ticket is assigned to another agent — only unowned (or just-served) tickets can be skipped.")
    now = _now()
    if t.assigned_agent_id and str(t.assigned_agent_id) == str(admin.id):
        t.assigned_agent_id = None
        if t.status == TicketStatus.IN_PROGRESS.value:
            sla_util.apply_pause_transition(t, TicketStatus.IN_PROGRESS.value, TicketStatus.OPEN.value, now)
            t.status = TicketStatus.OPEN.value
    db.add(SdTicketSkip(ticket_id=t.id, user_id=admin.id,
                        reason_code=payload.reason_code, note=payload.note))
    db.add(SdTicketActivity(
        ticket_id=t.id, actor_user_id=admin.id,
        actor_name=getattr(admin, "full_name", None) or "Agent",
        action="skipped",
        detail={"reason_code": payload.reason_code, "note": payload.note}))
    write_audit(db, entity_type="ticket", op="skipped", entity_id=t.id, actor_id=admin.id,
                request=request, details={"reason_code": payload.reason_code})
    db.commit()
    db.refresh(t)
    from app.routers.support_desk._common import enrich_ticket
    return TicketResponse.model_validate(enrich_ticket(db, t))


@ticket_tier_router.post("/{ticket_id}/tier-escalate", response_model=TicketResponse)
def tier_escalate(ticket_id: UUID, payload: TierEscalateRequest, request: Request,
                  db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Escalate a ticket UP the tier ladder (L1→L2→L3): re-parks it on the target
    tier's queue (category match first) and writes the standard structured escalation
    record via the shared engine. L3 handoffs require a technical diagnosis."""
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _do_escalate, _actor_name, dispatch_safe, _panel_base,
    )
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "escalate it")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Resolved/closed tickets can't be escalated — reopen it first.")
    cur_tier = _queue_tier_of(db, t)
    if cur_tier is not None and payload.to_tier <= cur_tier:
        raise HTTPException(422, f"This ticket is already at L{cur_tier} — use tier-descend to send it down.")
    if payload.to_tier >= 3 and not (payload.diagnosis or "").strip():
        raise HTTPException(422, "An L3 handoff needs a technical diagnosis — describe what was tried and what the suspected root cause is.")

    if payload.queue_id:
        target = db.query(SdQueue).filter(
            SdQueue.id == payload.queue_id, SdQueue.is_deleted == False,  # noqa: E712
            SdQueue.is_active == True).first()  # noqa: E712
        if not target or target.tier != payload.to_tier:
            raise HTTPException(422, f"queue_id must name an active L{payload.to_tier} queue.")
    else:
        target = find_tier_queue(db, payload.to_tier, t.category_id, exclude_id=t.queue_id, subcategory_id=t.subcategory_id)
        if not target:
            raise HTTPException(422, f"No active L{payload.to_tier} queue exists yet — create one in Queue Config first.")

    reason = payload.reason or f"Escalated to L{payload.to_tier} ({target.name})"
    _do_escalate(db, t, admin, reason,
                 reason_code=payload.reason_code or "complexity",
                 escalation_type=EscalationType.FUNCTIONAL.value,
                 to_team_id=target.team_id)
    apply_tier_move(db, t, target, actor_id=admin.id, actor_name=_actor_name(admin),
                    direction="escalate",
                    detail={"reason_code": payload.reason_code, "from_tier": cur_tier})
    if (payload.diagnosis or "").strip():
        db.add(SdTicketComment(
            ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
            author_kind=CommentAuthorKind.STAFF.value,
            body=f"[L{payload.to_tier} handoff — technical diagnosis]\n{payload.diagnosis.strip()}",
            is_internal=True))
    # The receiving tier's lead gets pinged — a functional escalation is a handoff.
    lead_pinged = None
    if target.team_id:
        tm = db.query(SdTeam).filter(SdTeam.id == target.team_id).first()
        if tm and tm.lead_user_id and str(tm.lead_user_id) != str(admin.id):
            dispatch_safe(db, EVT_TICKET_ESCALATED, tm.lead_user_id, t,
                          title=f"L{payload.to_tier} handoff: {t.subject}",
                          action_url=f"{_panel_base(db, tm.lead_user_id)}/queues/l{payload.to_tier}")
            lead_pinged = tm.lead_user_id
    # Watchers (notify-only followers) hear about tier moves too.
    from app.utils.support_desk.watchers import notify_ticket_watchers
    notify_ticket_watchers(db, t, EVT_TICKET_ESCALATED,
                           f"Ticket {t.ticket_number}: escalated to L{payload.to_tier}",
                           actor_id=admin.id,
                           exclude_ids=[t.assigned_agent_id, lead_pinged])
    write_audit(db, entity_type="ticket", op="tier_escalated", entity_id=t.id, actor_id=admin.id,
                request=request, details={"to_tier": payload.to_tier, "queue": target.name})
    db.commit()
    db.refresh(t)
    from app.routers.support_desk._common import enrich_ticket
    return TicketResponse.model_validate(enrich_ticket(db, t))


@ticket_tier_router.post("/{ticket_id}/tier-descend", response_model=TicketResponse)
def tier_descend(ticket_id: UUID, payload: TierDescendRequest, request: Request,
                 db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """De-escalate DOWN the ladder (L3→L2→L1): stands the escalation down via the
    shared writer and re-parks the ticket on the target tier's queue. If the current
    assignee isn't on the receiving team, the ticket returns to that queue's pool."""
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _do_de_escalate, _actor_name, dispatch_safe, _panel_base,
    )
    from app.routers.support_desk.tickets_self import _team_members_of
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "de-escalate it")
    if t.status in TERMINAL_TICKET_STATUSES:
        raise HTTPException(409, "Resolved/closed tickets can't be moved between tiers.")
    cur_tier = _queue_tier_of(db, t)
    if cur_tier is None:
        raise HTTPException(422, "This ticket isn't parked on a tiered queue — assign it a queue first.")
    if payload.to_tier >= cur_tier:
        raise HTTPException(422, f"This ticket is at L{cur_tier} — tier-descend only moves it to a LOWER tier.")
    if payload.reason_code and payload.reason_code not in TIER_DESCEND_REASON_CODES:
        raise HTTPException(422, f"reason_code must be one of {TIER_DESCEND_REASON_CODES}")

    if payload.queue_id:
        target = db.query(SdQueue).filter(
            SdQueue.id == payload.queue_id, SdQueue.is_deleted == False,  # noqa: E712
            SdQueue.is_active == True).first()  # noqa: E712
        if not target or target.tier != payload.to_tier:
            raise HTTPException(422, f"queue_id must name an active L{payload.to_tier} queue.")
    else:
        target = find_tier_queue(db, payload.to_tier, t.category_id, exclude_id=t.queue_id, subcategory_id=t.subcategory_id)
        if not target:
            raise HTTPException(422, f"No active L{payload.to_tier} queue exists yet — create one in Queue Config first.")

    reason = payload.reason or f"Sent back to L{payload.to_tier} ({payload.reason_code or 'de-escalated'})"
    if t.is_escalated:
        _do_de_escalate(db, t, admin, reason)
    apply_tier_move(db, t, target, actor_id=admin.id, actor_name=_actor_name(admin),
                    direction="descend",
                    detail={"reason_code": payload.reason_code, "from_tier": cur_tier})
    # Assignee not on the receiving team → back to the pool (never a stranded owner).
    if t.assigned_agent_id:
        members = {str(m) for m in _team_members_of(db, target.team_id)} if target.team_id else set()
        if str(t.assigned_agent_id) not in members:
            prev = t.assigned_agent_id
            t.assigned_agent_id = None
            db.add(SdTicketActivity(
                ticket_id=t.id, actor_user_id=admin.id, actor_name=_actor_name(admin),
                action="unassigned",
                detail={"by": "tier-descend", "previous_agent_id": str(prev)}))
    lead_pinged = None
    if target.team_id:
        tm = db.query(SdTeam).filter(SdTeam.id == target.team_id).first()
        if tm and tm.lead_user_id and str(tm.lead_user_id) != str(admin.id):
            dispatch_safe(db, EVT_TICKET_ASSIGNED, tm.lead_user_id, t,
                          title=f"Returned to L{payload.to_tier}: {t.subject}",
                          action_url=f"{_panel_base(db, tm.lead_user_id)}/queues/l{payload.to_tier}")
            lead_pinged = tm.lead_user_id
    from app.utils.support_desk.watchers import notify_ticket_watchers
    notify_ticket_watchers(db, t, EVT_TICKET_ASSIGNED,
                           f"Ticket {t.ticket_number}: sent back to L{payload.to_tier}",
                           actor_id=admin.id,
                           exclude_ids=[t.assigned_agent_id, lead_pinged])
    write_audit(db, entity_type="ticket", op="tier_descended", entity_id=t.id, actor_id=admin.id,
                request=request, details={"to_tier": payload.to_tier, "queue": target.name,
                                          "reason_code": payload.reason_code})
    db.commit()
    db.refresh(t)
    from app.routers.support_desk._common import enrich_ticket
    return TicketResponse.model_validate(enrich_ticket(db, t))


@ticket_tier_router.get("/skip-report", response_model=dict)
def skip_report(days: int = Query(7, ge=1, le=90), db: Session = Depends(get_db),
                admin: User = Depends(get_support_agent)):
    """Supervisor skip report (Zendesk guided-mode governance): who skipped how much,
    for which reasons — sealed to the caller's visible queues."""
    queues, _ctx = _visible_queues(db, admin, include_inactive=True)
    qids = [x.id for x in queues]
    since = _now() - timedelta(days=days)
    if not qids:
        return {"total": 0, "by_agent": [], "by_reason": {}, "recent": []}
    base = (db.query(SdTicketSkip)
            .join(SdTicket, SdTicket.id == SdTicketSkip.ticket_id)
            .filter(SdTicket.queue_id.in_(qids), SdTicketSkip.created_at >= since))
    rows = base.order_by(SdTicketSkip.created_at.desc()).limit(500).all()
    names = {str(r[0]): r[1] for r in db.query(User.id, User.full_name)
             .filter(User.id.in_({s.user_id for s in rows})).all()} if rows else {}
    numbers = {str(r[0]): r[1] for r in db.query(SdTicket.id, SdTicket.ticket_number)
               .filter(SdTicket.id.in_({s.ticket_id for s in rows})).all()} if rows else {}
    by_agent: dict = {}
    by_reason: dict = {}
    for s in rows:
        by_agent[str(s.user_id)] = by_agent.get(str(s.user_id), 0) + 1
        by_reason[s.reason_code] = by_reason.get(s.reason_code, 0) + 1
    return {
        "total": len(rows),
        "by_agent": sorted(({"user_id": k, "name": names.get(k), "count": v}
                            for k, v in by_agent.items()), key=lambda e: -e["count"]),
        "by_reason": by_reason,
        "recent": [{"ticket_id": str(s.ticket_id), "ticket_number": numbers.get(str(s.ticket_id)),
                    "user_id": str(s.user_id), "name": names.get(str(s.user_id)),
                    "reason_code": s.reason_code, "note": s.note,
                    "at": s.created_at.isoformat() if s.created_at else None} for s in rows[:25]],
    }
