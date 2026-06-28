"""Support Desk — admin dashboard / KPI aggregation. prefix=/support-desk/dashboard."""
from __future__ import annotations

from datetime import datetime, date, time, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket
from app.models.support_desk.constants import (
    TicketStatus, TicketPriority, OPEN_TICKET_STATUSES,
)
from app.schemas.support_desk.dashboard import SupportDashboardResponse
from app.utils.dependencies import get_support_agent

router = APIRouter(prefix="/support-desk/dashboard", tags=["Support Desk — Dashboard"])


@router.get("/", response_model=SupportDashboardResponse)
def support_dashboard(
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    base = db.query(SdTicket).filter(SdTicket.is_deleted == False)  # noqa: E712

    def count(q):
        return q.count()

    open_q = base.filter(SdTicket.status.in_(OPEN_TICKET_STATUSES))
    today_start = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)

    resp = SupportDashboardResponse()
    resp.total_tickets = count(base)
    resp.open_tickets = count(open_q)
    resp.unassigned = count(open_q.filter(SdTicket.assigned_agent_id.is_(None)))
    resp.pending = count(base.filter(SdTicket.status.in_([
        TicketStatus.PENDING_CUSTOMER.value, TicketStatus.PENDING_VENDOR.value])))
    resp.critical = count(open_q.filter(SdTicket.priority == TicketPriority.CRITICAL.value))
    resp.escalated = count(base.filter(SdTicket.is_escalated == True,  # noqa: E712
                                       SdTicket.status.in_(OPEN_TICKET_STATUSES)))
    resp.overdue = count(open_q.filter(SdTicket.sla_resolution_breached == True))  # noqa: E712
    resp.sla_breached = count(base.filter(or_(
        SdTicket.sla_response_breached == True, SdTicket.sla_resolution_breached == True)))  # noqa: E712
    resp.resolved_today = count(base.filter(SdTicket.resolved_at >= today_start))
    resp.closed_today = count(base.filter(SdTicket.closed_at >= today_start))

    # Averages (minutes)
    avg_resp = db.query(func.avg(func.extract("epoch", SdTicket.first_responded_at - SdTicket.created_at))) \
        .filter(SdTicket.is_deleted == False, SdTicket.first_responded_at.isnot(None)).scalar()  # noqa: E712
    avg_reso = db.query(func.avg(func.extract("epoch", SdTicket.resolved_at - SdTicket.created_at))) \
        .filter(SdTicket.is_deleted == False, SdTicket.resolved_at.isnot(None)).scalar()  # noqa: E712
    resp.avg_response_mins = round(float(avg_resp) / 60, 1) if avg_resp else None
    resp.avg_resolution_mins = round(float(avg_reso) / 60, 1) if avg_reso else None

    # CSAT — % of rated tickets scoring >= 4
    rated = db.query(func.count(SdTicket.id)).filter(
        SdTicket.is_deleted == False, SdTicket.csat_score.isnot(None)).scalar() or 0  # noqa: E712
    if rated:
        happy = db.query(func.count(SdTicket.id)).filter(
            SdTicket.is_deleted == False, SdTicket.csat_score >= 4).scalar() or 0  # noqa: E712
        resp.csat = round(happy / rated * 100, 1)

    # Distributions
    pr_rows = (open_q.with_entities(SdTicket.priority, func.count(SdTicket.id))
               .group_by(SdTicket.priority).all())
    resp.priority_counts = {p.value: 0 for p in TicketPriority}
    for k, v in pr_rows:
        resp.priority_counts[k] = v

    st_rows = (base.with_entities(SdTicket.status, func.count(SdTicket.id))
               .group_by(SdTicket.status).all())
    resp.status_counts = {k: v for k, v in st_rows}

    ty_rows = (base.with_entities(SdTicket.ticket_type, func.count(SdTicket.id))
               .group_by(SdTicket.ticket_type).all())
    resp.type_counts = {k: v for k, v in ty_rows}

    return resp
