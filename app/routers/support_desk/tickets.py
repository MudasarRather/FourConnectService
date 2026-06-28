"""Support Desk — admin/agent ticket router (the core).

CRUD + lifecycle (assign / status / escalate / resolve / reopen) + conversation
+ timeline, with SLA clocks, configurable numbering, audit and notifications.
All routes require a superadmin (admin panel). prefix=/support-desk/tickets.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketComment, SdTicketActivity
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, TicketType, TicketSource, CommentAuthorKind,
    OPEN_TICKET_STATUSES, TERMINAL_TICKET_STATUSES, PRIORITY_ORDER,
    EVT_TICKET_CREATED, EVT_TICKET_ASSIGNED, EVT_TICKET_REPLIED,
    EVT_TICKET_STATUS, EVT_TICKET_ESCALATED, EVT_TICKET_RESOLVED,
)
from app.schemas.support_desk.ticket import (
    TicketCreate, TicketUpdate, TicketResponse, TicketDetailResponse, TicketListResponse,
    TicketAssign, TicketStatusChange, TicketCsat, CommentCreate, CommentResponse, ActivityResponse,
)
from app.utils.dependencies import get_support_agent
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.routers.support_desk._common import (
    generate_ticket_number, resolve_sla_package, enrich_tickets, enrich_ticket,
)

router = APIRouter(prefix="/support-desk/tickets", tags=["Support Desk — Tickets"])

_PRIORITIES = {p.value for p in TicketPriority}
_TYPES = {t.value for t in TicketType}
_SOURCES = {s.value for s in TicketSource}
_STATUSES = {s.value for s in TicketStatus}
PORTAL_TOKEN_TTL_DAYS = 14


def _actor_name(user: User) -> str:
    return getattr(user, "full_name", None) or getattr(user, "email", None) or "Agent"


def _log_activity(db: Session, ticket: SdTicket, actor: User, action: str, detail: dict | None = None):
    db.add(SdTicketActivity(
        ticket_id=ticket.id, actor_user_id=actor.id if actor else None,
        actor_name=_actor_name(actor) if actor else "System",
        action=action, detail=detail or {},
    ))


def _get_ticket(db: Session, ticket_id: UUID) -> SdTicket:
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket not found")
    return t


# ─────────────────────────────── List ───────────────────────────────
@router.get("/", response_model=TicketListResponse)
def list_tickets(
    scope: Optional[str] = Query(None, description="all|my|unassigned|critical|escalated|pending|sla_breached|resolved|closed"),
    status_f: Optional[str] = Query(None, alias="status"),
    priority: Optional[str] = None,
    ticket_type: Optional[str] = None,
    organization_id: Optional[UUID] = None,
    assigned_agent_id: Optional[UUID] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    query = db.query(SdTicket).filter(SdTicket.is_deleted == False)  # noqa: E712

    if scope == "my":
        query = query.filter(SdTicket.assigned_agent_id == admin.id)
    elif scope == "unassigned":
        query = query.filter(SdTicket.assigned_agent_id.is_(None),
                             SdTicket.status.in_(OPEN_TICKET_STATUSES))
    elif scope == "critical":
        query = query.filter(SdTicket.priority == TicketPriority.CRITICAL.value)
    elif scope == "escalated":
        query = query.filter(SdTicket.is_escalated == True)  # noqa: E712
    elif scope == "pending":
        query = query.filter(SdTicket.status.in_([TicketStatus.PENDING_CUSTOMER.value,
                                                  TicketStatus.PENDING_VENDOR.value]))
    elif scope == "sla_breached":
        query = query.filter(or_(SdTicket.sla_response_breached == True,
                                 SdTicket.sla_resolution_breached == True))  # noqa: E712
    elif scope == "resolved":
        query = query.filter(SdTicket.status == TicketStatus.RESOLVED.value)
    elif scope == "closed":
        query = query.filter(SdTicket.status == TicketStatus.CLOSED.value)

    if status_f:
        query = query.filter(SdTicket.status == status_f)
    if priority:
        query = query.filter(SdTicket.priority == priority)
    if ticket_type:
        query = query.filter(SdTicket.ticket_type == ticket_type)
    if organization_id:
        query = query.filter(SdTicket.organization_id == organization_id)
    if assigned_agent_id:
        query = query.filter(SdTicket.assigned_agent_id == assigned_agent_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(SdTicket.subject.ilike(like), SdTicket.ticket_number.ilike(like)))

    total = query.count()
    items = (query.order_by(SdTicket.created_at.desc())
             .offset((page - 1) * limit).limit(limit).all())
    enrich_tickets(db, items)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in items],
        total=total, page=page, limit=limit,
    )


# ─────────────────────────────── Create ───────────────────────────────
@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_ticket(
    payload: TicketCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    if payload.priority not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{payload.priority}'")
    if payload.ticket_type not in _TYPES:
        raise HTTPException(422, f"Invalid ticket_type '{payload.ticket_type}'")
    if payload.source not in _SOURCES:
        raise HTTPException(422, f"Invalid source '{payload.source}'")

    number = generate_ticket_number(db)
    pkg = resolve_sla_package(db, payload.sla_package_id, payload.organization_id)
    rd, rsd = sla_util.compute_deadlines(pkg, payload.priority)

    t = SdTicket(
        ticket_number=number,
        subject=payload.subject,
        description=payload.description,
        category_id=payload.category_id,
        ticket_type=payload.ticket_type,
        priority=payload.priority,
        source=payload.source,
        status=TicketStatus.OPEN.value,
        organization_id=payload.organization_id,
        customer_id=payload.customer_id,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        contact_phone=payload.contact_phone,
        department=payload.department,
        location=payload.location,
        support_team=payload.support_team,
        assigned_agent_id=payload.assigned_agent_id,
        assigned_engineer_id=payload.assigned_engineer_id,
        assigned_pm_id=payload.assigned_pm_id,
        sla_package_id=pkg.id if pkg else payload.sla_package_id,
        response_due_at=rd,
        resolution_due_at=rsd,
        attachments=payload.attachments or [],
        tags=payload.tags or [],
        created_by_id=admin.id,
    )
    db.add(t)
    db.flush()  # assign id
    _log_activity(db, t, admin, "created", {"ticket_number": number, "priority": payload.priority})
    if t.assigned_agent_id:
        dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                      title=f"Assigned: {t.subject}", action_url=f"/admin/support-desk/tickets")
    write_audit(db, entity_type="ticket", op="created", entity_id=t.id,
                actor_id=admin.id, request=request,
                details={"ticket_number": number, "subject": t.subject, "priority": t.priority})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Detail ───────────────────────────────
@router.get("/{ticket_id}", response_model=TicketDetailResponse)
def get_ticket(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id)
    enrich_ticket(db, t)
    return TicketDetailResponse.model_validate(t)


# ─────────────────────────────── Update ───────────────────────────────
@router.patch("/{ticket_id}", response_model=TicketResponse)
def update_ticket(
    ticket_id: UUID,
    payload: TicketUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id)
    update = payload.model_dump(exclude_unset=True)
    if "priority" in update and update["priority"] not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{update['priority']}'")
    if "ticket_type" in update and update["ticket_type"] not in _TYPES:
        raise HTTPException(422, f"Invalid ticket_type '{update['ticket_type']}'")

    changed = {}
    for k, v in update.items():
        old = getattr(t, k, None)
        if old != v:
            changed[k] = {"from": str(old), "to": str(v)}
            setattr(t, k, v)

    # Re-arm SLA clocks if priority/package changed before first response.
    if ("priority" in update or "sla_package_id" in update) and t.first_responded_at is None:
        pkg = resolve_sla_package(db, t.sla_package_id, t.organization_id)
        rd, rsd = sla_util.compute_deadlines(pkg, t.priority, start=t.created_at)
        t.sla_package_id = pkg.id if pkg else t.sla_package_id
        t.response_due_at, t.resolution_due_at = rd, rsd

    if changed:
        _log_activity(db, t, admin, "updated", {"changes": changed})
        write_audit(db, entity_type="ticket", op="updated", entity_id=t.id,
                    actor_id=admin.id, request=request, details={"changes": changed})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Assign ───────────────────────────────
@router.post("/{ticket_id}/assign", response_model=TicketResponse)
def assign_ticket(
    ticket_id: UUID,
    payload: TicketAssign,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id)
    data = payload.model_dump(exclude_unset=True)
    prev_agent = t.assigned_agent_id
    for k, v in data.items():
        setattr(t, k, v)
    _log_activity(db, t, admin, "assigned", data)
    if t.assigned_agent_id and t.assigned_agent_id != prev_agent:
        dispatch_safe(db, EVT_TICKET_ASSIGNED, t.assigned_agent_id, t,
                      title=f"Assigned: {t.subject}", action_url="/admin/support-desk/tickets")
    write_audit(db, entity_type="ticket", op="assigned", entity_id=t.id,
                actor_id=admin.id, request=request, details=data)
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Status ───────────────────────────────
@router.post("/{ticket_id}/status", response_model=TicketResponse)
def change_status(
    ticket_id: UUID,
    payload: TicketStatusChange,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    new = payload.status
    if new not in _STATUSES:
        raise HTTPException(422, f"Invalid status '{new}'")
    t = _get_ticket(db, ticket_id)
    old = t.status
    if old == new:
        raise HTTPException(409, f"Ticket is already '{new}'")

    nowt = sla_util.now_utc()
    # First response: leaving 'open' for the first time counts.
    if old == TicketStatus.OPEN.value and t.first_responded_at is None:
        t.first_responded_at = nowt

    # Reopen from a terminal state.
    if old in TERMINAL_TICKET_STATUSES and new in OPEN_TICKET_STATUSES:
        t.reopened_count = (t.reopened_count or 0) + 1
        t.resolved_at = None
        t.closed_at = None

    if new == TicketStatus.RESOLVED.value:
        t.resolved_at = nowt
    elif new == TicketStatus.CLOSED.value:
        t.closed_at = nowt
        if t.resolved_at is None:
            t.resolved_at = nowt

    t.status = new
    sla_util.recompute_breach_flags(t, nowt)
    _log_activity(db, t, admin, "status_changed", {"from": old, "to": new, "note": payload.note})

    if t.raised_by_user_id:
        evt = EVT_TICKET_RESOLVED if new == TicketStatus.RESOLVED.value else EVT_TICKET_STATUS
        dispatch_safe(db, evt, t.raised_by_user_id, t,
                      title=f"Ticket {t.ticket_number}: {new.replace('_', ' ')}",
                      action_url="/user/support/tickets")
    write_audit(db, entity_type="ticket", op="status_changed", entity_id=t.id,
                actor_id=admin.id, request=request, details={"from": old, "to": new})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Escalate ───────────────────────────────
@router.post("/{ticket_id}/escalate", response_model=TicketResponse)
def escalate_ticket(
    ticket_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id)
    t.is_escalated = True
    t.escalation_level = (t.escalation_level or 0) + 1
    t.escalated_at = sla_util.now_utc()
    if t.status in OPEN_TICKET_STATUSES:
        t.status = TicketStatus.ESCALATED.value
    _log_activity(db, t, admin, "escalated", {"level": t.escalation_level})
    if t.assigned_agent_id:
        dispatch_safe(db, EVT_TICKET_ESCALATED, t.assigned_agent_id, t,
                      title=f"Escalated (L{t.escalation_level}): {t.subject}",
                      action_url="/admin/support-desk/tickets")
    write_audit(db, entity_type="ticket", op="escalated", entity_id=t.id,
                actor_id=admin.id, request=request, details={"level": t.escalation_level})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── CSAT ───────────────────────────────
@router.post("/{ticket_id}/csat", response_model=TicketResponse)
def set_csat(
    ticket_id: UUID,
    payload: TicketCsat,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id)
    t.csat_score = payload.csat_score
    t.csat_comment = payload.csat_comment
    _log_activity(db, t, admin, "csat", {"score": payload.csat_score})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


# ─────────────────────────────── Comments ───────────────────────────────
@router.get("/{ticket_id}/comments", response_model=list[CommentResponse])
def list_comments(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    _get_ticket(db, ticket_id)
    return (db.query(SdTicketComment).filter(SdTicketComment.ticket_id == ticket_id)
            .order_by(SdTicketComment.created_at).all())


@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def add_comment(
    ticket_id: UUID,
    payload: CommentCreate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id)
    c = SdTicketComment(
        ticket_id=t.id, author_user_id=admin.id, author_name=_actor_name(admin),
        author_kind=CommentAuthorKind.STAFF.value, body=payload.body,
        is_internal=payload.is_internal, attachments=payload.attachments or [],
    )
    db.add(c)
    # First public staff reply stops the response clock.
    if not payload.is_internal and t.first_responded_at is None:
        t.first_responded_at = sla_util.now_utc()
        sla_util.recompute_breach_flags(t)
    _log_activity(db, t, admin, "internal_note" if payload.is_internal else "replied",
                  {"preview": payload.body[:80]})
    if not payload.is_internal and t.raised_by_user_id:
        dispatch_safe(db, EVT_TICKET_REPLIED, t.raised_by_user_id, t,
                      title=f"Reply on {t.ticket_number}", action_url="/user/support/tickets")
    write_audit(db, entity_type="ticket", op="commented", entity_id=t.id,
                actor_id=admin.id, request=request, details={"internal": payload.is_internal})
    db.commit()
    db.refresh(c)
    return c


@router.get("/{ticket_id}/activities", response_model=list[ActivityResponse])
def list_activities(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    _get_ticket(db, ticket_id)
    return (db.query(SdTicketActivity).filter(SdTicketActivity.ticket_id == ticket_id)
            .order_by(SdTicketActivity.created_at).all())


# ─────────────────────────────── Delete + portal ───────────────────────────────
@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ticket(
    ticket_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    t = _get_ticket(db, ticket_id)
    t.is_deleted = True
    write_audit(db, entity_type="ticket", op="deleted", entity_id=t.id,
                actor_id=admin.id, request=request, details={"ticket_number": t.ticket_number})
    db.commit()
    return None


@router.post("/{ticket_id}/portal/rotate")
def rotate_portal_token(
    ticket_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    """Mint/refresh the public-portal token + security window for client access."""
    t = _get_ticket(db, ticket_id)
    t.public_token = secrets.token_urlsafe(32)
    t.public_token_expires_at = sla_util.now_utc() + timedelta(days=PORTAL_TOKEN_TTL_DAYS)
    db.commit()
    return {"public_token": t.public_token, "public_token_expires_at": t.public_token_expires_at}


# ─────────────────────────────── notify helper ───────────────────────────────
def dispatch_safe(db: Session, event: str, recipient_user_id, ticket: SdTicket, *,
                  title: str, action_url: str):
    """Thin wrapper around HR notify.dispatch — Support Desk's first production caller."""
    try:
        from app.utils.hr.notify import dispatch
        # Deep-link straight to the ticket so the bell click opens its drawer.
        deep = f"{action_url}{'&' if '?' in action_url else '?'}ticket={ticket.id}"
        dispatch(db, event, recipient_user_id,
                 context={"title": title, "message": f"{ticket.ticket_number}: {ticket.subject}",
                          "action_url": deep},
                 audience="SUPPORT")
    except Exception:
        pass
