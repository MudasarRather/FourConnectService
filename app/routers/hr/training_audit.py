"""HR Training & Development — Audit log (read-only)."""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.training_audit_log import TrainingAuditLog, TrainingAuditAction
from app.schemas.hr.training_audit import (
    TrainingAuditLogResponse, TrainingAuditListResponse,
)
from app.utils.hr.training.service import user_name
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Audit"])


@router.get("/audit-logs", response_model=TrainingAuditListResponse)
def list_audit_logs(
    entity_type: Optional[str] = None,
    entity_id: Optional[UUID] = None,
    action: Optional[TrainingAuditAction] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(TrainingAuditLog)
    if entity_type:
        q = q.filter(TrainingAuditLog.entity_type == entity_type)
    if entity_id:
        q = q.filter(TrainingAuditLog.entity_id == entity_id)
    if action:
        q = q.filter(TrainingAuditLog.action == action)
    if date_from:
        q = q.filter(TrainingAuditLog.created_at >= datetime.combine(date_from, time.min))
    if date_to:
        q = q.filter(TrainingAuditLog.created_at <= datetime.combine(date_to, time.max))
    total = q.count()
    rows = (
        q.order_by(TrainingAuditLog.created_at.desc())
        .offset((page - 1) * limit).limit(limit).all()
    )
    items = [
        TrainingAuditLogResponse(
            id=r.id, entity_type=r.entity_type, entity_id=r.entity_id, action=r.action,
            actor_id=r.actor_id, actor_name=user_name(db, r.actor_id),
            from_status=r.from_status, to_status=r.to_status, payload=r.payload,
            note=r.note, created_at=r.created_at,
        )
        for r in rows
    ]
    return TrainingAuditListResponse(items=items, total=total, page=page, limit=limit)
