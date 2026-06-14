"""HR Attendance Audit Logs — read-only."""
from __future__ import annotations

from math import ceil
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.attendance_log import AttendanceLog, AttendanceLogAction
from app.schemas.hr.attendance import AttendanceLogResponse, AttendanceLogListResponse
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/attendance/logs", tags=["HR — Attendance Logs"])


def _to_response(db: Session, l: AttendanceLog) -> AttendanceLogResponse:
    actor_name = None
    if l.actor_user_id:
        row = db.query(User.full_name).filter(User.id == l.actor_user_id).first()
        actor_name = row[0] if row else None
    return AttendanceLogResponse(
        id=l.id, actor_user_id=l.actor_user_id, actor_name=actor_name,
        action=l.action, target_table=l.target_table, target_id=l.target_id,
        employee_id=l.employee_id, payload=l.payload or {},
        created_at=l.created_at,
    )


@router.get("/", response_model=AttendanceLogListResponse)
def list_logs(
    action: Optional[AttendanceLogAction] = None,
    target_table: Optional[List[str]] = Query(None),
    employee_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AttendanceLog)
    if action:
        q = q.filter(AttendanceLog.action == action)
    # Filter by one or more target tables — lets the Shifts audit view pull every
    # shift-scoped event (assignments, rotations, rosters, swaps, OT rules) in one
    # time-ordered call regardless of the action enum used to record each.
    if target_table:
        q = q.filter(AttendanceLog.target_table.in_(target_table))
    if employee_id:
        q = q.filter(AttendanceLog.employee_id == employee_id)
    total = q.count()
    rows = q.order_by(AttendanceLog.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AttendanceLogListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )
