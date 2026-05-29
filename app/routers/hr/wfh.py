"""HR Work-from-Home / Remote requests."""
from __future__ import annotations

from datetime import date, datetime, timezone
from math import ceil
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.wfh_request import WfhRequest, WfhStatus
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.attendance import (
    WfhCreate, WfhUpdate, WfhDecideBody, WfhResponse, WfhListResponse,
)
from app.utils.dependencies import get_current_user, get_current_superuser
from app.utils.hr.attendance_logic import daily_rollup, log

router = APIRouter(prefix="/hr/wfh", tags=["HR — WFH"])


def _to_response(db: Session, w: WfhRequest) -> WfhResponse:
    name = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == w.employee_id)
        .first()
    )
    return WfhResponse(
        id=w.id, employee_id=w.employee_id,
        employee_name=name[0] if name else None,
        request_type=w.request_type,
        wfh_date=w.wfh_date,
        wfh_date_until=w.wfh_date_until,
        reason=w.reason,
        work_summary=w.work_summary,
        status=w.status,
        manager_approved_by_id=w.manager_approved_by_id,
        manager_approved_at=w.manager_approved_at,
        decision_notes=w.decision_notes,
        created_at=w.created_at,
    )


def _resolve_self(db: Session, user: User) -> Employee:
    emp = db.query(Employee).filter(Employee.user_id == user.id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "No employee profile linked to your account")
    return emp


@router.get("/", response_model=WfhListResponse)
def list_wfh(
    status_filter: Optional[WfhStatus] = Query(None, alias="status"),
    employee_id: Optional[UUID] = None,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(WfhRequest).filter(WfhRequest.is_deleted == False)  # noqa: E712
    if status_filter:
        q = q.filter(WfhRequest.status == status_filter)
    if employee_id:
        q = q.filter(WfhRequest.employee_id == employee_id)
    if from_:
        q = q.filter(WfhRequest.wfh_date >= from_)
    if to:
        q = q.filter(WfhRequest.wfh_date <= to)
    total = q.count()
    rows = q.order_by(WfhRequest.wfh_date.desc()).offset((page - 1) * limit).limit(limit).all()
    return WfhListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.get("/me", response_model=WfhListResponse)
def my_wfh(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self(db, user)
    rows = (
        db.query(WfhRequest)
        .filter(WfhRequest.employee_id == emp.id, WfhRequest.is_deleted == False)  # noqa: E712
        .order_by(WfhRequest.wfh_date.desc())
        .all()
    )
    return WfhListResponse(
        items=[_to_response(db, r) for r in rows],
        total=len(rows), page=1, limit=len(rows), total_pages=1,
    )


@router.post("/me", response_model=WfhResponse, status_code=http_status.HTTP_201_CREATED)
def create_my_wfh(
    payload: WfhCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self(db, user)
    if payload.wfh_date_until and payload.wfh_date_until < payload.wfh_date:
        raise HTTPException(422, "wfh_date_until must be >= wfh_date")
    w = WfhRequest(
        employee_id=emp.id,
        request_type=payload.request_type,
        wfh_date=payload.wfh_date,
        wfh_date_until=payload.wfh_date_until,
        reason=payload.reason,
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    return _to_response(db, w)


@router.get("/{wfh_id}", response_model=WfhResponse)
def get_wfh(
    wfh_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    w = db.query(WfhRequest).filter(WfhRequest.id == wfh_id, WfhRequest.is_deleted == False).first()  # noqa: E712
    if not w:
        raise HTTPException(404, "WFH request not found")
    return _to_response(db, w)


@router.patch("/{wfh_id}/approve", response_model=WfhResponse)
def approve_wfh(
    wfh_id: UUID,
    body: WfhDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    w = db.query(WfhRequest).filter(WfhRequest.id == wfh_id, WfhRequest.is_deleted == False).first()  # noqa: E712
    if not w:
        raise HTTPException(404, "WFH request not found")
    if w.status != WfhStatus.PENDING:
        raise HTTPException(409, f"WFH request already {w.status.value}")
    if w.wfh_date < date.today():
        raise HTTPException(422, "Cannot approve a WFH request for a past date")
    w.status = WfhStatus.APPROVED
    w.manager_approved_by_id = admin.id
    w.manager_approved_at = datetime.now(timezone.utc)
    w.decision_notes = body.notes

    # Recompute attendance rows in the WFH window so they reflect status=WFH
    end_date = w.wfh_date_until or w.wfh_date
    from datetime import timedelta
    d = w.wfh_date
    while d <= end_date:
        daily_rollup(db, w.employee_id, d, actor_id=admin.id)
        d = d + timedelta(days=1)

    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.WFH_APPROVED,
        target_table="hr_wfh_requests",
        target_id=w.id,
        employee_id=w.employee_id,
        payload={"from": w.wfh_date.isoformat(), "until": end_date.isoformat()},
    )
    db.commit()
    db.refresh(w)
    return _to_response(db, w)


@router.patch("/{wfh_id}/reject", response_model=WfhResponse)
def reject_wfh(
    wfh_id: UUID,
    body: WfhDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    w = db.query(WfhRequest).filter(WfhRequest.id == wfh_id, WfhRequest.is_deleted == False).first()  # noqa: E712
    if not w:
        raise HTTPException(404, "WFH request not found")
    if w.status != WfhStatus.PENDING:
        raise HTTPException(409, f"WFH request already {w.status.value}")
    w.status = WfhStatus.REJECTED
    w.manager_approved_by_id = admin.id
    w.manager_approved_at = datetime.now(timezone.utc)
    w.decision_notes = body.notes
    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.WFH_REJECTED,
        target_table="hr_wfh_requests",
        target_id=w.id,
        employee_id=w.employee_id,
        payload={"reason": body.notes},
    )
    db.commit()
    db.refresh(w)
    return _to_response(db, w)


@router.patch("/{wfh_id}/complete", response_model=WfhResponse)
def complete_wfh(
    wfh_id: UUID,
    body: WfhUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """User submits a work summary after WFH is over."""
    emp = _resolve_self(db, user)
    w = db.query(WfhRequest).filter(WfhRequest.id == wfh_id, WfhRequest.is_deleted == False).first()  # noqa: E712
    if not w:
        raise HTTPException(404, "WFH request not found")
    if w.employee_id != emp.id:
        raise HTTPException(403, "Cannot modify another employee's WFH request")
    if w.status != WfhStatus.APPROVED:
        raise HTTPException(409, "Only APPROVED WFH requests can be marked complete")
    if body.work_summary:
        w.work_summary = body.work_summary
    w.status = WfhStatus.COMPLETED
    db.commit()
    db.refresh(w)
    return _to_response(db, w)


@router.delete("/{wfh_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_wfh(
    wfh_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    w = db.query(WfhRequest).filter(WfhRequest.id == wfh_id).first()
    if not w:
        raise HTTPException(404, "WFH request not found")
    emp = db.query(Employee).filter(Employee.user_id == user.id).first()
    is_owner = emp and w.employee_id == emp.id
    if not (user.is_superuser or (is_owner and w.status == WfhStatus.PENDING)):
        raise HTTPException(403, "Only superusers or owners of PENDING requests can delete")
    w.is_deleted = True
    db.commit()
