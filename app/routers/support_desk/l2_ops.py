"""Support Desk — L2 workbench router: worklogs, watchers, swarm sessions.

ServiceNow/Zendesk parity surfaces for the specialist (L2) desk, usable from any
ticket surface. All routes are agent-gated and TEAM-SEALED via the same
``_get_ticket(db, id, admin)`` scope check the broad tickets router uses (404 —
not 403 — outside the seal, so ticket existence doesn't leak).

Registered in ``routers/support_desk/__init__.py`` BEFORE the broad tickets router
(route-shadowing discipline; these paths all carry literal suffixes after the id).

Gates at a glance:
  • worklog create        owner-tier (mirror of POST /{id}/time — a teammate must not
                          pad the effort record of a ticket they don't work);
                          allowed on RESOLVED (post-resolution work notes), blocked
                          on CLOSED + merged tombstones
  • worklog delete        the entry's author or a superuser
  • watch / unwatch       self-service, idempotent, any in-scope agent
  • swarm start / end     owner-tier (end also honours the initiator); one ACTIVE
                          session per ticket (409 on double-start)
  • swarm join            any in-scope agent; joining also grants collaborator
                          act-rights via ``SdTicket.collaborators``
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketComment
from app.models.support_desk.collab import SdTicketWorklog, SdTicketWatcher, SdSwarmSession
from app.models.support_desk.constants import TicketStatus, CommentAuthorKind, EVT_TICKET_ASSIGNED
from app.schemas.support_desk.l2 import (
    WorklogCreate, WorklogResponse, WorklogListResponse,
    WatcherEntry, WatchersResponse, WatchToggleResponse, WatcherAdd,
    SwarmStartRequest, SwarmEndRequest, SwarmParticipant, SwarmResponse, SwarmStateResponse,
)
from app.utils.dependencies import get_support_agent
from app.utils.support_desk.audit import write_audit

l2_router = APIRouter(prefix="/support-desk/tickets", tags=["Support Desk — L2 Workbench"])


# ─────────────────────────────── helpers ───────────────────────────────
def _guard_live(t: SdTicket, *, allow_resolved: bool = False, action: str = "do that"):
    """Terminal-state guard shared by every mutation here."""
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — work on the surviving ticket instead.")
    if t.status == TicketStatus.CLOSED.value:
        raise HTTPException(409, f"This ticket is closed — reopen it to {action}.")
    if not allow_resolved and t.status == TicketStatus.RESOLVED.value:
        raise HTTPException(409, f"This ticket is resolved — reopen it to {action}.")


def _names_of(db: Session, ids) -> dict:
    ids = [i for i in {str(x) for x in ids if x}]
    if not ids:
        return {}
    rows = db.query(User.id, User.full_name).filter(User.id.in_(ids)).all()
    return {str(uid): (name or "Agent") for uid, name in rows}


def _worklog_dto(w: SdTicketWorklog, names: dict) -> WorklogResponse:
    dto = WorklogResponse.model_validate(w)
    dto.user_name = names.get(str(w.user_id), "Agent")
    return dto


def _swarm_dto(s: SdSwarmSession, names: dict) -> SwarmResponse:
    dto = SwarmResponse.model_validate(s)
    dto.started_by_name = names.get(str(s.started_by_id), "Agent")
    dto.participants = [SwarmParticipant(user_id=UUID(str(u)), user_name=names.get(str(u), "Agent"))
                        for u in (s.participant_ids or [])]
    return dto


def _live_worklogs(db: Session, ticket_id: UUID):
    return (db.query(SdTicketWorklog)
            .filter(SdTicketWorklog.ticket_id == ticket_id,
                    SdTicketWorklog.is_deleted == False))  # noqa: E712


def _active_swarm(db: Session, ticket_id: UUID) -> Optional[SdSwarmSession]:
    return (db.query(SdSwarmSession)
            .filter(SdSwarmSession.ticket_id == ticket_id,
                    SdSwarmSession.status == "active")
            .order_by(SdSwarmSession.started_at.desc()).first())


# ─────────────────────────────── Worklogs ───────────────────────────────
@l2_router.post("/{ticket_id}/worklogs", response_model=WorklogResponse)
def add_worklog(ticket_id: UUID, payload: WorklogCreate, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    _guard_live(t, allow_resolved=True, action="log time on it")
    _require_ticket_actor(db, t, admin, "log time on it")
    w = SdTicketWorklog(ticket_id=t.id, user_id=admin.id, minutes=payload.minutes,
                        note=(payload.note or None), work_type=payload.work_type)
    db.add(w)
    # Keep the legacy cumulative counter authoritative for every existing surface.
    t.time_spent_minutes = (t.time_spent_minutes or 0) + payload.minutes
    _log_activity(db, t, admin, "time_logged",
                  {"minutes": payload.minutes, "note": payload.note,
                   "work_type": payload.work_type})
    db.commit()
    db.refresh(w)
    return _worklog_dto(w, _names_of(db, [w.user_id]))


@l2_router.get("/{ticket_id}/worklogs", response_model=WorklogListResponse)
def list_worklogs(ticket_id: UUID,
                  page: int = Query(1, ge=1), limit: int = Query(25, ge=1, le=100),
                  db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)  # scope seal; reading is team-open
    base = _live_worklogs(db, t.id)
    total = base.count()
    total_minutes = int(base.with_entities(func.coalesce(func.sum(SdTicketWorklog.minutes), 0)).scalar() or 0)
    rows = (base.order_by(SdTicketWorklog.created_at.desc())
            .offset((page - 1) * limit).limit(limit).all())
    names = _names_of(db, [r.user_id for r in rows])
    return WorklogListResponse(items=[_worklog_dto(r, names) for r in rows],
                               total=total, total_minutes=total_minutes)


@l2_router.delete("/{ticket_id}/worklogs/{worklog_id}", response_model=WorklogListResponse)
def delete_worklog(ticket_id: UUID, worklog_id: UUID, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import _get_ticket, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    w = (db.query(SdTicketWorklog)
         .filter(SdTicketWorklog.id == worklog_id, SdTicketWorklog.ticket_id == t.id,
                 SdTicketWorklog.is_deleted == False).first())  # noqa: E712
    if not w:
        raise HTTPException(404, "Worklog entry not found")
    if str(w.user_id) != str(admin.id) and not getattr(admin, "is_superuser", False):
        raise HTTPException(403, "Only the author (or an admin) can remove a worklog entry.")
    w.is_deleted = True
    # Decrement the cumulative counter, floored at 0 (legacy /time entries have no rows).
    t.time_spent_minutes = max(0, (t.time_spent_minutes or 0) - (w.minutes or 0))
    _log_activity(db, t, admin, "time_log_removed", {"minutes": w.minutes, "work_type": w.work_type})
    write_audit(db, entity_type="ticket", op="worklog_removed", entity_id=t.id, actor_id=admin.id,
                request=request, details={"worklog_id": str(w.id), "minutes": w.minutes})
    db.commit()
    return list_worklogs(ticket_id, 1, 25, db, admin)  # fresh first page for the ledger


# ─────────────────────────────── Watchers ───────────────────────────────
@l2_router.get("/{ticket_id}/watchers", response_model=WatchersResponse)
def list_watchers(ticket_id: UUID, db: Session = Depends(get_db),
                  admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    rows = (db.query(SdTicketWatcher)
            .filter(SdTicketWatcher.ticket_id == t.id)
            .order_by(SdTicketWatcher.created_at.asc()).all())
    names = _names_of(db, [r.user_id for r in rows])
    items = [WatcherEntry(user_id=r.user_id, user_name=names.get(str(r.user_id), "Agent"),
                          created_at=r.created_at) for r in rows]
    return WatchersResponse(items=items, total=len(items),
                            watching=any(str(r.user_id) == str(admin.id) for r in rows))


@l2_router.post("/{ticket_id}/watch", response_model=WatchToggleResponse)
def watch_ticket(ticket_id: UUID, db: Session = Depends(get_db),
                 admin: User = Depends(get_support_agent)):
    """Self-service follow — idempotent (double-watch returns the existing state).
    Watching a resolved/closed ticket is allowed (e.g. to hear about a reopen);
    only merged tombstones refuse (the surviving ticket is the one to watch)."""
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — watch the surviving ticket instead.")
    exists = (db.query(SdTicketWatcher)
              .filter(SdTicketWatcher.ticket_id == t.id,
                      SdTicketWatcher.user_id == admin.id).first())
    if not exists:
        db.add(SdTicketWatcher(ticket_id=t.id, user_id=admin.id))
        db.commit()
    total = db.query(func.count(SdTicketWatcher.id)).filter(SdTicketWatcher.ticket_id == t.id).scalar() or 0
    return WatchToggleResponse(watching=True, total=int(total))


@l2_router.delete("/{ticket_id}/watch", response_model=WatchToggleResponse)
def unwatch_ticket(ticket_id: UUID, db: Session = Depends(get_db),
                   admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    (db.query(SdTicketWatcher)
     .filter(SdTicketWatcher.ticket_id == t.id, SdTicketWatcher.user_id == admin.id)
     .delete(synchronize_session=False))
    db.commit()
    total = db.query(func.count(SdTicketWatcher.id)).filter(SdTicketWatcher.ticket_id == t.id).scalar() or 0
    return WatchToggleResponse(watching=False, total=int(total))


@l2_router.post("/{ticket_id}/watchers", response_model=WatchToggleResponse)
def add_watcher(ticket_id: UUID, payload: WatcherAdd, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Subscribe ANOTHER user as a stakeholder (incident comms hub) — owner-tier only;
    self-service follow stays on POST /watch. Idempotent: an existing subscription
    returns the current state. Watching a resolved ticket is allowed (reopen news);
    merged tombstones refuse."""
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    _require_ticket_actor(db, t, admin, "subscribe a stakeholder to it")
    if t.merged_into_id:
        raise HTTPException(409, "This ticket was merged — subscribe on the surviving ticket instead.")
    u = db.query(User).filter(User.id == payload.user_id, User.is_active == True).first()  # noqa: E712
    if not u:
        raise HTTPException(400, "Stakeholder user not found or inactive")
    exists = (db.query(SdTicketWatcher)
              .filter(SdTicketWatcher.ticket_id == t.id,
                      SdTicketWatcher.user_id == payload.user_id).first())
    if not exists:
        db.add(SdTicketWatcher(ticket_id=t.id, user_id=payload.user_id))
        _log_activity(db, t, admin, "watcher_added",
                      {"user": u.full_name or str(payload.user_id)})
        write_audit(db, entity_type="ticket", op="watcher_added", entity_id=t.id,
                    actor_id=admin.id, request=request, details={"user_id": str(payload.user_id)})
        db.commit()
    total = db.query(func.count(SdTicketWatcher.id)).filter(SdTicketWatcher.ticket_id == t.id).scalar() or 0
    return WatchToggleResponse(watching=True, total=int(total))


@l2_router.delete("/{ticket_id}/watchers/{user_id}", response_model=WatchToggleResponse)
def remove_watcher(ticket_id: UUID, user_id: UUID, request: Request,
                   db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    """Unsubscribe a stakeholder. Self-removal is always allowed (parity with
    DELETE /watch); removing SOMEONE ELSE is owner-tier. Idempotent."""
    from app.routers.support_desk.tickets import _get_ticket, _require_ticket_actor, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    if str(user_id) != str(admin.id):
        _require_ticket_actor(db, t, admin, "manage its stakeholder subscriptions")
    removed = (db.query(SdTicketWatcher)
               .filter(SdTicketWatcher.ticket_id == t.id, SdTicketWatcher.user_id == user_id)
               .delete(synchronize_session=False))
    if removed:
        names = _names_of(db, [user_id])
        _log_activity(db, t, admin, "watcher_removed",
                      {"user": names.get(str(user_id), str(user_id))})
        write_audit(db, entity_type="ticket", op="watcher_removed", entity_id=t.id,
                    actor_id=admin.id, request=request, details={"user_id": str(user_id)})
    db.commit()
    total = db.query(func.count(SdTicketWatcher.id)).filter(SdTicketWatcher.ticket_id == t.id).scalar() or 0
    return WatchToggleResponse(watching=False, total=int(total))


# ─────────────────────────────── Swarm ───────────────────────────────
@l2_router.get("/{ticket_id}/swarm", response_model=SwarmStateResponse)
def swarm_state(ticket_id: UUID, db: Session = Depends(get_db),
                admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import _get_ticket
    t = _get_ticket(db, ticket_id, admin)
    sessions = (db.query(SdSwarmSession)
                .filter(SdSwarmSession.ticket_id == t.id)
                .order_by(SdSwarmSession.started_at.desc()).limit(10).all())
    ids = {s.started_by_id for s in sessions} | {u for s in sessions for u in (s.participant_ids or [])}
    names = _names_of(db, ids)
    active = next((s for s in sessions if s.status == "active"), None)
    history = [_swarm_dto(s, names) for s in sessions if s.status != "active"]
    return SwarmStateResponse(
        active=_swarm_dto(active, names) if active else None,
        history=history,
        joined=bool(active and str(admin.id) in {str(u) for u in (active.participant_ids or [])}))


@l2_router.post("/{ticket_id}/swarm", response_model=SwarmStateResponse)
def swarm_start(ticket_id: UUID, payload: SwarmStartRequest, request: Request,
                db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _log_activity, dispatch_safe, _panel_base,
    )
    t = _get_ticket(db, ticket_id, admin)
    _guard_live(t, action="start a swarm on it")
    _require_ticket_actor(db, t, admin, "start a swarm on it")
    if _active_swarm(db, t.id):
        raise HTTPException(409, "A swarm is already running on this ticket — join it instead.")
    s = SdSwarmSession(ticket_id=t.id, started_by_id=admin.id, participant_ids=[str(admin.id)])
    db.add(s)
    _log_activity(db, t, admin, "swarm_started", {"note": payload.note})
    # The owner should know their ticket just went multi-agent (unless they started it).
    if t.assigned_agent_id and str(t.assigned_agent_id) != str(admin.id):
        dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                      title=f"Swarm started on {t.ticket_number}",
                      action_url=f"{_panel_base(db, t.assigned_agent_id)}/queues/l2")
    write_audit(db, entity_type="ticket", op="swarm_started", entity_id=t.id, actor_id=admin.id,
                request=request, details={"note": payload.note})
    db.commit()
    return swarm_state(ticket_id, db, admin)


@l2_router.post("/{ticket_id}/swarm/join", response_model=SwarmStateResponse)
def swarm_join(ticket_id: UUID, db: Session = Depends(get_db),
               admin: User = Depends(get_support_agent)):
    """Any in-scope agent may answer the call. Joining is idempotent. Participants gain
    owner-tier act rights for the DURATION of the swarm (the actor gates consult
    ``_in_active_swarm``) — join no longer writes the agent permanently into
    ``collaborators`` (which left stale owner-tier long after the swarm ended)."""
    from app.routers.support_desk.tickets import _get_ticket, _log_activity
    t = _get_ticket(db, ticket_id, admin)
    _guard_live(t, action="join a swarm on it")
    s = _active_swarm(db, t.id)
    if not s:
        raise HTTPException(409, "No active swarm on this ticket — start one first.")
    uid = str(admin.id)
    if uid not in {str(u) for u in (s.participant_ids or [])}:
        s.participant_ids = list(s.participant_ids or []) + [uid]
        flag_modified(s, "participant_ids")
        _log_activity(db, t, admin, "swarm_joined", {})
        db.commit()
    return swarm_state(ticket_id, db, admin)


@l2_router.post("/{ticket_id}/swarm/end", response_model=SwarmStateResponse)
def swarm_end(ticket_id: UUID, payload: SwarmEndRequest, request: Request,
              db: Session = Depends(get_db), admin: User = Depends(get_support_agent)):
    from app.routers.support_desk.tickets import (
        _get_ticket, _require_ticket_actor, _log_activity, _actor_name,
    )
    from app.utils.support_desk import sla as sla_util
    t = _get_ticket(db, ticket_id, admin)
    s = _active_swarm(db, t.id)
    if not s:
        raise HTTPException(409, "No active swarm on this ticket.")
    # The initiator may always stand their own swarm down; otherwise owner-tier.
    if str(s.started_by_id) != str(admin.id):
        _require_ticket_actor(db, t, admin, "end the swarm on it")
    s.status = "ended"
    s.ended_at = sla_util.now_utc()
    s.ended_by_id = admin.id
    s.outcome = (payload.outcome or None)
    if (payload.outcome or "").strip():
        db.add(SdTicketComment(
            ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
            author_kind=CommentAuthorKind.STAFF.value,
            body=f"[Swarm outcome]\n{payload.outcome.strip()}",
            is_internal=True))
    _log_activity(db, t, admin, "swarm_ended",
                  {"outcome": payload.outcome, "participants": len(s.participant_ids or [])})
    write_audit(db, entity_type="ticket", op="swarm_ended", entity_id=t.id, actor_id=admin.id,
                request=request, details={"participants": len(s.participant_ids or [])})
    db.commit()
    return swarm_state(ticket_id, db, admin)
