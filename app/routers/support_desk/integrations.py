"""Support Desk — ERP integration seams (admin).

  • Ticket  → Project Task   (Task has nullable project_id, so standalone is fine)
  • Service Request → Invoice (ProjectPayment REQUIRES a project_id — caller supplies one)

Attachments → Drive is handled by the existing /drive/upload + the ticket's
attachments JSON; notifications already flow via app.utils.hr.notify.dispatch().
prefix=/support-desk (admin).
"""
from __future__ import annotations

from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db
from app.models.user import User
from app.models.support_desk.ticket import SdTicket, SdTicketActivity
from app.models.support_desk.catalog import SdServiceRequest
from app.utils.dependencies import get_current_superuser
from app.utils.support_desk.audit import write_audit

router = APIRouter(prefix="/support-desk", tags=["Support Desk — ERP Integrations"])

# Ticket and TaskPriority share the same five values — preserve fidelity 1:1.
_PRI_MAP = {"low": "low", "medium": "medium", "high": "high", "urgent": "urgent", "critical": "critical"}


class ToTaskBody(BaseModel):
    project_id: Optional[UUID] = None
    assigned_to: Optional[UUID] = None


class ToInvoiceBody(BaseModel):
    project_id: UUID
    amount: float
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None


@router.post("/tickets/{ticket_id}/to-task")
def ticket_to_task(
    ticket_id: UUID,
    body: ToTaskBody,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Spin a project Task off a ticket and remember the link on ticket.links."""
    t = db.query(SdTicket).filter(SdTicket.id == ticket_id, SdTicket.is_deleted == False).first()  # noqa: E712
    if not t:
        raise HTTPException(404, "Ticket not found")

    from app.models.task import Task, TaskPriority
    kwargs = dict(
        title=f"[{t.ticket_number}] {t.subject}"[:255],
        description=t.description,
        created_by=admin.id,
        assigned_by=admin.id,
        assigned_to=body.assigned_to or t.assigned_agent_id,
        project_id=body.project_id,
    )
    try:
        kwargs["priority"] = TaskPriority(_PRI_MAP.get(t.priority, "medium"))
    except Exception:
        pass  # fall back to the column default
    task = Task(**kwargs)
    db.add(task)
    db.flush()

    links = dict(t.links or {})
    links["task_id"] = str(task.id)
    t.links = links
    flag_modified(t, "links")

    db.add(SdTicketActivity(ticket_id=t.id, actor_user_id=admin.id,
                            actor_name=getattr(admin, "full_name", None) or "Agent",
                            action="linked_task", detail={"task_id": str(task.id)}))
    write_audit(db, entity_type="ticket", op="to_task", entity_id=t.id, actor_id=admin.id,
                request=request, details={"task_id": str(task.id)})
    db.commit()
    return {"task_id": str(task.id), "task_code": getattr(task, "task_code", None),
            "title": task.title}


@router.post("/service-requests/{req_id}/to-invoice")
def service_request_to_invoice(
    req_id: UUID,
    body: ToInvoiceBody,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Raise a ProjectPayment (invoice line) from a service request against a project."""
    sr = db.query(SdServiceRequest).filter(
        SdServiceRequest.id == req_id, SdServiceRequest.is_deleted == False).first()  # noqa: E712
    if not sr:
        raise HTTPException(404, "Service request not found")

    from app.models.project import Project
    proj = db.query(Project).filter(Project.id == body.project_id, Project.is_deleted == False).first()  # noqa: E712
    if not proj:
        raise HTTPException(400, "Project not found")

    from app.models.financials import ProjectPayment
    pay = ProjectPayment(
        project_id=body.project_id,
        vendor_name=body.vendor_name or "Support Desk Service",
        amount_paid=body.amount,
        payment_date=date.today(),
        status="Pending",
        invoice_number=body.invoice_number,
        created_by_id=admin.id,
    )
    db.add(pay)
    db.flush()

    data = dict(sr.data or {})
    data["invoice_payment_id"] = str(pay.id)
    sr.data = data
    flag_modified(sr, "data")
    write_audit(db, entity_type="service_request", op="to_invoice", entity_id=sr.id, actor_id=admin.id,
                request=request, details={"payment_id": str(pay.id), "project_id": str(body.project_id)})
    db.commit()
    return {"payment_id": str(pay.id), "project_id": str(body.project_id), "amount": body.amount}
