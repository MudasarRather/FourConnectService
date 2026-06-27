"""Support Desk — employee self-service. prefix=/support-desk/me/tickets (auth=user).

Employees raise internal tickets, track status, reply, and rate resolution.
Registered BEFORE the broad admin tickets router (different prefix, but order-safe).
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketComment
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, TicketType, TicketSource, CommentAuthorKind,
    OPEN_TICKET_STATUSES, EVT_TICKET_CREATED, EVT_TICKET_REPLIED,
)
from app.schemas.support_desk.ticket import (
    TicketCreate, TicketResponse, TicketDetailResponse, TicketListResponse,
    TicketCsat, CommentCreate, CommentResponse,
)
from app.schemas.support_desk.dashboard import SelfDashboardResponse
from app.utils.dependencies import get_current_user
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.routers.support_desk._common import (
    generate_ticket_number, resolve_sla_package, enrich_tickets, enrich_ticket,
)

router = APIRouter(prefix="/support-desk/me/tickets", tags=["Support Desk — My Tickets"])

_PRIORITIES = {p.value for p in TicketPriority}
_TYPES = {t.value for t in TicketType}


def _own(db: Session, ticket_id: UUID, user: User) -> SdTicket:
    t = db.query(SdTicket).filter(
        SdTicket.id == ticket_id, SdTicket.is_deleted == False,  # noqa: E712
        SdTicket.raised_by_user_id == user.id,
    ).first()
    if not t:
        raise HTTPException(404, "Ticket not found")
    return t


@router.get("/dashboard", response_model=SelfDashboardResponse)
def my_dashboard(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    base = db.query(SdTicket).filter(
        SdTicket.is_deleted == False, SdTicket.raised_by_user_id == user.id)  # noqa: E712
    out = SelfDashboardResponse()
    out.total = base.count()
    out.open = base.filter(SdTicket.status == TicketStatus.OPEN.value).count()
    out.in_progress = base.filter(SdTicket.status == TicketStatus.IN_PROGRESS.value).count()
    out.pending = base.filter(SdTicket.status.in_([
        TicketStatus.PENDING_CUSTOMER.value, TicketStatus.PENDING_VENDOR.value])).count()
    out.resolved = base.filter(SdTicket.status.in_([
        TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value])).count()
    pr_rows = (base.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES))
               .with_entities(SdTicket.priority, func.count(SdTicket.id))
               .group_by(SdTicket.priority).all())
    out.priority_counts = {p.value: 0 for p in TicketPriority}
    for k, v in pr_rows:
        out.priority_counts[k] = v
    return out


@router.get("/", response_model=TicketListResponse)
def list_my_tickets(
    scope: Optional[str] = Query(None, description="all|open|resolved"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    q = db.query(SdTicket).filter(
        SdTicket.is_deleted == False, SdTicket.raised_by_user_id == user.id)  # noqa: E712
    if scope == "open":
        q = q.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES))
    elif scope == "resolved":
        q = q.filter(SdTicket.status.in_([TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value]))
    total = q.count()
    items = q.order_by(SdTicket.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    enrich_tickets(db, items)
    return TicketListResponse(
        items=[TicketResponse.model_validate(t) for t in items],
        total=total, page=page, limit=limit,
    )


@router.post("/", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
def create_my_ticket(
    payload: TicketCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.priority not in _PRIORITIES:
        raise HTTPException(422, f"Invalid priority '{payload.priority}'")
    if payload.ticket_type not in _TYPES:
        raise HTTPException(422, f"Invalid ticket_type '{payload.ticket_type}'")

    number = generate_ticket_number(db)
    pkg = resolve_sla_package(db, payload.sla_package_id, None)
    rd, rsd = sla_util.compute_deadlines(pkg, payload.priority)

    t = SdTicket(
        ticket_number=number,
        subject=payload.subject,
        description=payload.description,
        category_id=payload.category_id,
        ticket_type=payload.ticket_type,
        priority=payload.priority,
        source=TicketSource.PORTAL.value,
        status=TicketStatus.OPEN.value,
        is_internal=True,
        raised_by_user_id=user.id,
        contact_name=getattr(user, "full_name", None),
        contact_email=getattr(user, "email", None),
        sla_package_id=pkg.id if pkg else None,
        response_due_at=rd, resolution_due_at=rsd,
        attachments=payload.attachments or [],
        tags=payload.tags or [],
        created_by_id=user.id,
    )
    db.add(t)
    db.flush()
    from app.models.support_desk.ticket import SdTicketActivity
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Employee",
                            action="created", detail={"ticket_number": number}))
    write_audit(db, entity_type="ticket", op="created", entity_id=t.id,
                actor_id=user.id, request=request,
                details={"ticket_number": number, "self_service": True})
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
def get_my_ticket(ticket_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    t = _own(db, ticket_id, user)
    enrich_ticket(db, t)
    resp = TicketDetailResponse.model_validate(t)
    resp.comments = [c for c in resp.comments if not c.is_internal]  # hide internal notes
    return resp


@router.post("/{ticket_id}/comments", response_model=CommentResponse, status_code=status.HTTP_201_CREATED)
def reply_my_ticket(
    ticket_id: UUID,
    payload: CommentCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _own(db, ticket_id, user)
    c = SdTicketComment(
        ticket_id=t.id, author_user_id=user.id,
        author_name=getattr(user, "full_name", None) or "Employee",
        author_kind=CommentAuthorKind.CUSTOMER.value, body=payload.body,
        is_internal=False, attachments=payload.attachments or [],
    )
    db.add(c)
    from app.models.support_desk.ticket import SdTicketActivity
    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=user.id,
                            actor_name=getattr(user, "full_name", None) or "Employee",
                            action="replied", detail={"preview": payload.body[:80]}))
    # Notify the assigned agent of the requester's reply.
    if t.assigned_agent_id:
        try:
            from app.utils.hr.notify import dispatch
            dispatch(db, EVT_TICKET_REPLIED, t.assigned_agent_id,
                     context={"title": f"Customer reply on {t.ticket_number}",
                              "message": t.subject, "action_url": f"/admin/support-desk/tickets?ticket={t.id}"},
                     audience="SUPPORT")
        except Exception:
            pass
    write_audit(db, entity_type="ticket", op="commented", entity_id=t.id,
                actor_id=user.id, request=request, details={"by": "requester"})
    db.commit()
    db.refresh(c)
    return c


@router.post("/{ticket_id}/csat", response_model=TicketResponse)
def rate_my_ticket(
    ticket_id: UUID,
    payload: TicketCsat,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    t = _own(db, ticket_id, user)
    if t.status not in (TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value):
        raise HTTPException(409, "You can only rate a resolved ticket")
    t.csat_score = payload.csat_score
    t.csat_comment = payload.csat_comment
    db.commit()
    db.refresh(t)
    return TicketResponse.model_validate(enrich_ticket(db, t))
