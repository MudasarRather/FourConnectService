"""Support Desk — PUBLIC client portal (no auth; token-gated capability).

Mirrors the exit-document portal: an external client submits a ticket (gated by
their organization code + email) and tracks/replies to it via an unguessable
``public_token``. No JWT — the org code (submit) and token (view/reply) ARE the
security boundary. fourreck.com links here. prefix=/public/support.
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.support_desk.core import SdOrganization, SdCustomer
from app.models.support_desk.ticket import SdTicket, SdTicketComment, SdTicketActivity
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, TicketType, TicketSource, CommentAuthorKind,
    EVT_TICKET_REPLIED,
)
from app.schemas.support_desk.ticket import PublicTicketCreate, PublicCommentCreate
from app.utils.support_desk import sla as sla_util
from app.utils.support_desk.audit import write_audit
from app.routers.support_desk._common import (
    generate_ticket_number, resolve_sla_package, reactivate_on_customer_reply,
    auto_reopen_on_customer_reply,
)

router = APIRouter(prefix="/public/support", tags=["Support Desk — Public Portal"])

PORTAL_TOKEN_TTL_DAYS = 30
_PRIORITIES = {p.value for p in TicketPriority}

# ── Rate limiting (no-auth surface = brute-force / spam target) ──
# In-process sliding window per client IP. Fine for this single-process backend
# (StaticPool = one worker); swap for slowapi/Redis if the app ever scales out.
_RL_BUCKETS: dict = {}
_RL_MAX_KEYS = 10_000   # hard cap so a spoofed-IP flood can't balloon memory


def _throttle(request: Request, bucket: str, limit: int, window_s: int) -> None:
    import time
    from collections import deque
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() \
        or (request.client.host if request.client else "?")
    key = f"{bucket}:{ip}"
    now = time.monotonic()
    dq = _RL_BUCKETS.get(key)
    if dq is None:
        if len(_RL_BUCKETS) >= _RL_MAX_KEYS:
            _RL_BUCKETS.clear()   # crude but bounded — better than unbounded growth
        dq = _RL_BUCKETS[key] = deque()
    while dq and now - dq[0] > window_s:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(429, "Too many requests — please wait a moment and try again.")
    dq.append(now)


def _portal_ticket(db: Session, token: str) -> SdTicket:
    t = db.query(SdTicket).filter(
        SdTicket.public_token == token, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket link not found")
    exp = t.public_token_expires_at
    if exp is not None:
        exp_aware = exp if exp.tzinfo else exp.replace(tzinfo=sla_util.now_utc().tzinfo)
        if sla_util.now_utc() > exp_aware:
            raise HTTPException(410, "This ticket link has expired. Please contact support for a fresh link.")
    return t


def _public_view(db: Session, t: SdTicket) -> dict:
    org_name = None
    if t.organization_id:
        org = db.query(SdOrganization.name).filter(SdOrganization.id == t.organization_id).first()
        org_name = org[0] if org else None
    comments = (db.query(SdTicketComment)
                .filter(SdTicketComment.ticket_id == t.id, SdTicketComment.is_internal == False)  # noqa: E712
                .order_by(SdTicketComment.created_at).all())
    return {
        "ticket_number": t.ticket_number,
        "subject": t.subject,
        "description": t.description,
        "status": t.status,
        "priority": t.priority,
        "organization_name": org_name,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "resolved_at": t.resolved_at,
        "resolution_state": sla_util.resolution_state(t),
        "expires_at": t.public_token_expires_at,
        "comments": [
            {"author_name": c.author_name, "author_kind": c.author_kind,
             "body": c.body, "created_at": c.created_at}
            for c in comments
        ],
    }


@router.post("/tickets")
def submit_public_ticket(payload: PublicTicketCreate, request: Request, db: Session = Depends(get_db)):
    """Public submission. Gated by a valid, active organization code."""
    _throttle(request, "submit", limit=5, window_s=600)     # 5 tickets / 10 min / IP
    if payload.priority not in _PRIORITIES:
        payload.priority = TicketPriority.MEDIUM.value
    code = (payload.org_code or "").strip()
    org = db.query(SdOrganization).filter(
        SdOrganization.code == code, SdOrganization.is_active == True,  # noqa: E712
        SdOrganization.is_deleted == False).first()  # noqa: E712
    if not org:
        raise HTTPException(404, "Organization code not recognized. Please check with your account manager.")

    # Best-effort: link a known contact by email.
    customer = None
    if payload.email:
        customer = db.query(SdCustomer).filter(
            SdCustomer.organization_id == org.id,
            SdCustomer.email.ilike(payload.email.strip()),
            SdCustomer.is_deleted == False).first()  # noqa: E712

    number = generate_ticket_number(db)
    pkg = resolve_sla_package(db, None, org.id)
    rd, rsd = sla_util.compute_deadlines(pkg, payload.priority)

    t = SdTicket(
        ticket_number=number,
        subject=payload.subject,
        description=payload.description,
        ticket_type=TicketType.INCIDENT.value,
        priority=payload.priority,
        source=TicketSource.PORTAL.value,
        status=TicketStatus.OPEN.value,
        organization_id=org.id,
        customer_id=customer.id if customer else None,
        contact_name=payload.contact_name or (customer.name if customer else None),
        contact_email=payload.email,
        contact_phone=payload.contact_phone,
        sla_package_id=pkg.id if pkg else None,
        response_due_at=rd, resolution_due_at=rsd,
        attachments=payload.attachments or [],
        public_token=secrets.token_urlsafe(32),
        public_token_expires_at=sla_util.now_utc() + timedelta(days=PORTAL_TOKEN_TTL_DAYS),
    )
    db.add(t)
    db.flush()
    db.add(SdTicketActivity(ticket_id=t.id, actor_name=payload.contact_name or payload.email or "Client",
                            action="created", detail={"via": "public_portal"}))
    write_audit(db, entity_type="ticket", op="created", entity_id=t.id, request=request,
                details={"via": "public_portal", "org_code": code})
    db.commit()
    db.refresh(t)
    return {
        "ticket_number": t.ticket_number,
        "public_token": t.public_token,
        "status": t.status,
        "track_url": f"/support/portal/{t.public_token}",
    }


@router.get("/tickets/{token}")
def view_public_ticket(token: str, request: Request, db: Session = Depends(get_db)):
    _throttle(request, "view", limit=60, window_s=60)       # also slows token enumeration
    t = _portal_ticket(db, token)
    return _public_view(db, t)


@router.post("/tickets/{token}/comments")
def reply_public_ticket(token: str, payload: PublicCommentCreate, request: Request, db: Session = Depends(get_db)):
    _throttle(request, "reply", limit=20, window_s=600)     # 20 replies / 10 min / IP
    t = _portal_ticket(db, token)
    db.add(SdTicketComment(
        ticket_id=t.id, author_name=t.contact_name or "Client",
        author_kind=CommentAuthorKind.CUSTOMER.value, body=payload.body,
        is_internal=False, attachments=payload.attachments or [],
    ))
    t.last_customer_reply_at = sla_util.now_utc()
    db.add(SdTicketActivity(ticket_id=t.id, actor_name=t.contact_name or "Client",
                            action="replied", detail={"via": "public_portal"}))
    # Loophole fix: a client reply through the portal reactivates an awaiting-customer ticket
    # (out of pending_customer into active work) so it re-enters the agent's queue. And a
    # reply to a RESOLVED ticket inside the reopen window auto-reopens it (source='portal')
    # — the fix evidently didn't hold; without this the reply lands silently and the
    # auto-close sweep buries the ticket days later.
    if not reactivate_on_customer_reply(db, t):
        auto_reopen_on_customer_reply(db, t, t.contact_name or "Client")
    if t.assigned_agent_id:
        try:
            from app.utils.hr.notify import dispatch
            dispatch(db, EVT_TICKET_REPLIED, t.assigned_agent_id,
                     context={"title": f"Client reply on {t.ticket_number}", "message": t.subject,
                              "action_url": f"/admin/support-desk/tickets?ticket={t.id}"},
                     audience="SUPPORT")
        except Exception:
            pass
    write_audit(db, entity_type="ticket", op="commented", entity_id=t.id, request=request,
                details={"via": "public_portal"})
    db.commit()
    return {"ok": True}
