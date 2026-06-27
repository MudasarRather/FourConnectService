"""Support Desk — shared router helpers: ticket numbering, SLA-package
resolution, and batched enrichment of ticket responses (display names + SLA
states + comment counts) so admin/self/public routers stay DRY and N+1-free.
"""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.support_desk.core import SdOrganization, SdCustomer, SdCategory, SdSlaPackage
from app.models.support_desk.ticket import SdTicket, SdTicketComment
from app.utils.support_desk import sla as sla_util


def generate_ticket_number(db: Session) -> str:
    """Prefer the configured NumberingSeries; fall back to a TKT+hex id."""
    try:
        from app.utils.hr.numbering import next_number
        n = next_number(db, "SUPPORT_TICKET")
        if n:
            return n
    except Exception:
        pass
    return f"TKT{uuid.uuid4().hex[:8].upper()}"


def resolve_sla_package(db: Session, explicit_id=None, organization_id=None) -> SdSlaPackage | None:
    """explicit package > organization's package > default package."""
    if explicit_id:
        pkg = db.query(SdSlaPackage).filter(
            SdSlaPackage.id == explicit_id, SdSlaPackage.is_deleted == False  # noqa: E712
        ).first()
        if pkg:
            return pkg
    if organization_id:
        org = db.query(SdOrganization).filter(SdOrganization.id == organization_id).first()
        if org and org.sla_package_id:
            pkg = db.query(SdSlaPackage).filter(SdSlaPackage.id == org.sla_package_id).first()
            if pkg:
                return pkg
    return db.query(SdSlaPackage).filter(
        SdSlaPackage.is_default == True, SdSlaPackage.is_deleted == False  # noqa: E712
    ).first()


def _user_names(db: Session, ids: set) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = db.query(User.id, User.full_name).filter(User.id.in_(ids)).all()
    return {str(r[0]): r[1] for r in rows}


def enrich_tickets(db: Session, tickets: Iterable[SdTicket]) -> list[SdTicket]:
    """Attach display names, SLA states and comment counts onto each ticket
    instance (read by Pydantic ``from_attributes``). Batched lookups."""
    tickets = list(tickets)
    if not tickets:
        return tickets

    org_ids = {t.organization_id for t in tickets if t.organization_id}
    cust_ids = {t.customer_id for t in tickets if t.customer_id}
    cat_ids = {t.category_id for t in tickets if t.category_id}
    user_ids = set()
    for t in tickets:
        user_ids.update([t.assigned_agent_id, t.raised_by_user_id])

    orgs = {str(o.id): o.name for o in db.query(SdOrganization.id, SdOrganization.name)
            .filter(SdOrganization.id.in_(org_ids)).all()} if org_ids else {}
    custs = {str(c.id): c.name for c in db.query(SdCustomer.id, SdCustomer.name)
             .filter(SdCustomer.id.in_(cust_ids)).all()} if cust_ids else {}
    cats = {str(c.id): c.name for c in db.query(SdCategory.id, SdCategory.name)
            .filter(SdCategory.id.in_(cat_ids)).all()} if cat_ids else {}
    users = _user_names(db, user_ids)

    counts = {}
    tids = [t.id for t in tickets]
    if tids:
        rows = (db.query(SdTicketComment.ticket_id, func.count(SdTicketComment.id))
                .filter(SdTicketComment.ticket_id.in_(tids))
                .group_by(SdTicketComment.ticket_id).all())
        counts = {str(r[0]): r[1] for r in rows}

    for t in tickets:
        t.organization_name = orgs.get(str(t.organization_id)) if t.organization_id else None
        t.customer_name = custs.get(str(t.customer_id)) if t.customer_id else None
        t.category_name = cats.get(str(t.category_id)) if t.category_id else None
        t.assigned_agent_name = users.get(str(t.assigned_agent_id)) if t.assigned_agent_id else None
        t.raised_by_name = users.get(str(t.raised_by_user_id)) if t.raised_by_user_id else None
        t.sla_response_state = sla_util.response_state(t)
        t.sla_resolution_state = sla_util.resolution_state(t)
        t.comment_count = counts.get(str(t.id), 0)
    return tickets


def enrich_ticket(db: Session, ticket: SdTicket) -> SdTicket:
    enrich_tickets(db, [ticket])
    return ticket
