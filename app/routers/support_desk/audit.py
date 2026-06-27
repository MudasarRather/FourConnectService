"""Support Desk — Audit Logs reader. Surfaces the ``support.*`` slice of the shared
audit_logs table with actor names + parsed detail. prefix=/support-desk/audit-logs.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.audit_log import AuditLog
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/support-desk/audit-logs", tags=["Support Desk — Audit Logs"])


@router.get("/")
def list_audit_logs(
    action: Optional[str] = None,
    entity_type: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    query = db.query(AuditLog).filter(AuditLog.action.like("support.%"))
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
