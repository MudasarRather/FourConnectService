"""HR Attendance Policies — SKELETON for Phase 2.X."""
from __future__ import annotations

from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.attendance_policy import AttendancePolicy, PolicyType
from app.schemas.hr.attendance import (
    AttendancePolicyCreate, AttendancePolicyResponse, AttendancePolicyListResponse,
)
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/attendance/policies", tags=["HR — Attendance Policies"])


def _to_response(p: AttendancePolicy) -> AttendancePolicyResponse:
    return AttendancePolicyResponse(
        id=p.id, name=p.name, policy_type=p.policy_type, description=p.description,
        rules=p.rules or {},
        applicable_department_ids=p.applicable_department_ids or [],
        applicable_shift_ids=p.applicable_shift_ids or [],
        effective_from=p.effective_from, effective_until=p.effective_until,
        is_active=bool(p.is_active), created_at=p.created_at,
    )


@router.get("/", response_model=AttendancePolicyListResponse)
def list_policies(
    policy_type: Optional[PolicyType] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AttendancePolicy).filter(AttendancePolicy.is_deleted == False)  # noqa: E712
    if policy_type:
        q = q.filter(AttendancePolicy.policy_type == policy_type)
    total = q.count()
    rows = q.order_by(AttendancePolicy.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return AttendancePolicyListResponse(
        items=[_to_response(r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.post("/", response_model=AttendancePolicyResponse, status_code=http_status.HTTP_201_CREATED)
def create_policy(
    payload: AttendancePolicyCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    p = AttendancePolicy(**payload.model_dump(), created_by_id=admin.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _to_response(p)


@router.delete("/{policy_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_policy(
    policy_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    p = db.query(AttendancePolicy).filter(AttendancePolicy.id == policy_id).first()
    if not p:
        raise HTTPException(404, "Policy not found")
    p.is_deleted = True
    db.commit()
