"""Support Desk — Audit Logs reader. Surfaces the ``support.*`` slice of the shared
audit_logs table with actor names + parsed detail. prefix=/support-desk/audit-logs.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from sqlalchemy import and_, or_

from app.database import get_db
from app.models.user import User
from app.models.audit_log import AuditLog
from app.models.support_desk.ticket import SdTicket
from app.utils.dependencies import get_support_agent

router = APIRouter(prefix="/support-desk/audit-logs", tags=["Support Desk — Audit Logs"])


@router.get("/")
def list_audit_logs(
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_support_agent),
):
    query = db.query(AuditLog).filter(AuditLog.action.like("support.%"))

    # Team-seal (was a desk-wide leak): a non-superuser agent must not page the whole
    # desk's audit trail. Scope them to the ticket ledger the agent UI is built for —
    # audit rows for tickets in their _agent_scope — plus their own actions on any
    # entity. Cross-team ticket trails and desk-wide config audit (problems / queues /
    # teams / changes) stay superuser-only. entity_type is stored as "support.<entity>"
    # (see utils/support_desk/audit.write_audit).
    if not getattr(admin, "is_superuser", False):
        from app.routers.support_desk.tickets import _agent_scope
        cond, _ctx = _agent_scope(db, admin)
        scoped_ticket_ids = db.query(SdTicket.id).filter(cond).scalar_subquery()
        query = query.filter(or_(
            and_(AuditLog.entity_type == "support.ticket",
                 AuditLog.entity_id.in_(scoped_ticket_ids)),
            AuditLog.user_id == admin.id,
        ))
    if action:
        query = query.filter(AuditLog.action == action)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if q:
        query = query.filter(AuditLog.details.ilike(f"%{q.strip()}%"))

    total = query.count()
    rows = (query.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * limit).limit(limit).all())

    user_ids = {r.user_id for r in rows if r.user_id}
    names = {}
    if user_ids:
        names = {str(u[0]): u[1] for u in db.query(User.id, User.full_name).filter(User.id.in_(user_ids)).all()}

    def _detail(raw):
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {"raw": raw}

    items = [{
        "id": str(r.id),
        "action": r.action,
        "entity_type": r.entity_type,
        "entity_id": str(r.entity_id) if r.entity_id else None,
        "user_id": str(r.user_id) if r.user_id else None,
        "actor_name": names.get(str(r.user_id)) if r.user_id else None,
        "detail": _detail(r.details),
        "created_at": r.created_at,
    } for r in rows]

    return {"items": items, "total": total, "page": page, "limit": limit}
