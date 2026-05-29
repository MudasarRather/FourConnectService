"""HR Attendance Corrections — manager → HR two-level approval workflow."""
from __future__ import annotations

from datetime import datetime, timezone
from math import ceil
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.attendance import Attendance, AttendanceSource
from app.models.hr.attendance_correction import AttendanceCorrection, CorrectionStatus
from app.models.hr.attendance_punch import AttendancePunch, PunchType
from app.models.hr.attendance_log import AttendanceLogAction
from app.schemas.hr.attendance import (
    CorrectionCreate, CorrectionDecideBody, CorrectionResponse, CorrectionListResponse,
)
from app.utils.dependencies import get_current_user, get_current_superuser
from app.utils.hr.attendance_logic import daily_rollup, log

router = APIRouter(prefix="/hr/attendance/corrections", tags=["HR — Attendance Corrections"])


def _to_response(db: Session, c: AttendanceCorrection) -> CorrectionResponse:
    name = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == c.employee_id)
        .first()
    )
    return CorrectionResponse(
        id=c.id,
        employee_id=c.employee_id,
        employee_name=name[0] if name else None,
        attendance_id=c.attendance_id,
        attendance_date=c.attendance_date,
        original_check_in=c.original_check_in,
        original_check_out=c.original_check_out,
        requested_check_in=c.requested_check_in,
        requested_check_out=c.requested_check_out,
        reason=c.reason,
        attachment_url=c.attachment_url,
        status=c.status,
        manager_approved_by_id=c.manager_approved_by_id,
        manager_approved_at=c.manager_approved_at,
        hr_approved_by_id=c.hr_approved_by_id,
        hr_approved_at=c.hr_approved_at,
        decision_notes=c.decision_notes,
        created_at=c.created_at,
    )


def _resolve_self(db: Session, user: User) -> Employee:
    emp = db.query(Employee).filter(Employee.user_id == user.id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "No employee profile linked to your account")
    return emp


# ── Admin ──────────────────────────────────────────────────────────────────

@router.get("/", response_model=CorrectionListResponse)
def list_corrections(
    status_filter: Optional[CorrectionStatus] = Query(None, alias="status"),
    employee_id: Optional[UUID] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(AttendanceCorrection).filter(AttendanceCorrection.is_deleted == False)  # noqa: E712
    if status_filter:
        q = q.filter(AttendanceCorrection.status == status_filter)
    if employee_id:
        q = q.filter(AttendanceCorrection.employee_id == employee_id)
    total = q.count()
    rows = q.order_by(AttendanceCorrection.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    return CorrectionListResponse(
        items=[_to_response(db, c) for c in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.get("/me", response_model=CorrectionListResponse)
def my_corrections(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self(db, user)
    q = (
        db.query(AttendanceCorrection)
        .filter(AttendanceCorrection.employee_id == emp.id, AttendanceCorrection.is_deleted == False)  # noqa: E712
        .order_by(AttendanceCorrection.created_at.desc())
    )
    rows = q.all()
    return CorrectionListResponse(
        items=[_to_response(db, c) for c in rows],
        total=len(rows), page=1, limit=len(rows), total_pages=1,
    )


@router.get("/{correction_id}", response_model=CorrectionResponse)
def get_correction(
    correction_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    c = db.query(AttendanceCorrection).filter(AttendanceCorrection.id == correction_id, AttendanceCorrection.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Correction not found")
    return _to_response(db, c)


@router.post("/me", response_model=CorrectionResponse, status_code=http_status.HTTP_201_CREATED)
def create_my_correction(
    payload: CorrectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self(db, user)
    att = (
        db.query(Attendance)
        .filter(Attendance.employee_id == emp.id, Attendance.date == payload.attendance_date,
                Attendance.is_deleted == False)  # noqa: E712
        .first()
    )
    c = AttendanceCorrection(
        employee_id=emp.id,
        attendance_id=att.id if att else None,
        attendance_date=payload.attendance_date,
        original_check_in=att.check_in_time if att else None,
        original_check_out=att.check_out_time if att else None,
        requested_check_in=payload.requested_check_in,
        requested_check_out=payload.requested_check_out,
        reason=payload.reason,
        attachment_url=payload.attachment_url,
    )
    db.add(c)
    db.flush()
    log(
        db,
        actor_id=user.id,
        action=AttendanceLogAction.CORRECTION_REQUESTED,
        target_table="hr_attendance_corrections",
        target_id=c.id,
        employee_id=emp.id,
        payload={"date": payload.attendance_date.isoformat()},
    )
    db.commit()
    db.refresh(c)
    return _to_response(db, c)


@router.patch("/{correction_id}/approve", response_model=CorrectionResponse)
def approve_correction(
    correction_id: UUID,
    body: CorrectionDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    c = db.query(AttendanceCorrection).filter(AttendanceCorrection.id == correction_id, AttendanceCorrection.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Correction not found")
    if c.status != CorrectionStatus.PENDING:
        raise HTTPException(409, f"Correction already {c.status.value}")

    # Apply to Attendance row (refuse on locked unless force)
    att = (
        db.query(Attendance)
        .filter(Attendance.employee_id == c.employee_id, Attendance.date == c.attendance_date)
        .first()
    )
    if att and att.is_locked and not body.force:
        raise HTTPException(409, "Linked attendance row is locked; resend with force=true to override")

    is_late_punch = (c.reason or "").startswith("[LATE_PUNCH]")

    if att:
        if c.requested_check_in is not None:
            att.check_in_time = c.requested_check_in
        if c.requested_check_out is not None:
            att.check_out_time = c.requested_check_out
        att.source = AttendanceSource.MANUAL
        att.last_updated_by_id = admin.id
        att.remarks = (att.remarks or "") + (f"\n[Correction approved] {body.notes or ''}".rstrip())
    elif is_late_punch and c.requested_check_in is not None:
        # Late-punch flow: no attendance row yet. Materialise a real IN punch
        # at the requested time and let the rollup compute LATE status + hours.
        punch = AttendancePunch(
            employee_id=c.employee_id,
            punch_time=c.requested_check_in,
            punch_type=PunchType.IN,
            source=AttendanceSource.MANUAL,
            geo_verified=True,  # admin approval implies verified
            payload={"approved_correction_id": str(c.id), "kind": "LATE_PUNCH"},
        )
        db.add(punch)
        db.flush()
        daily_rollup(db, c.employee_id, c.attendance_date, actor_id=admin.id, source=AttendanceSource.MANUAL)
        log(
            db,
            actor_id=admin.id,
            action=AttendanceLogAction.PUNCH,
            target_table="hr_attendance_punches",
            target_id=punch.id,
            employee_id=c.employee_id,
            payload={"type": "IN", "kind": "LATE_PUNCH_APPROVED", "correction_id": str(c.id)},
        )

    now = datetime.now(timezone.utc)
    if (body.level or "HR").upper() == "MANAGER":
        c.manager_approved_by_id = admin.id
        c.manager_approved_at = now
    else:
        c.hr_approved_by_id = admin.id
        c.hr_approved_at = now
    c.status = CorrectionStatus.APPROVED
    c.decision_notes = body.notes

    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.CORRECTION_APPROVED,
        target_table="hr_attendance_corrections",
        target_id=c.id,
        employee_id=c.employee_id,
        payload={"level": body.level, "force": body.force, "date": c.attendance_date.isoformat()},
    )
    db.commit()
    db.refresh(c)
    return _to_response(db, c)


@router.patch("/{correction_id}/reject", response_model=CorrectionResponse)
def reject_correction(
    correction_id: UUID,
    body: CorrectionDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    c = db.query(AttendanceCorrection).filter(AttendanceCorrection.id == correction_id, AttendanceCorrection.is_deleted == False).first()  # noqa: E712
    if not c:
        raise HTTPException(404, "Correction not found")
    if c.status != CorrectionStatus.PENDING:
        raise HTTPException(409, f"Correction already {c.status.value}")
    c.status = CorrectionStatus.REJECTED
    c.decision_notes = body.notes
    now = datetime.now(timezone.utc)
    if (body.level or "HR").upper() == "MANAGER":
        c.manager_approved_by_id = admin.id
        c.manager_approved_at = now
    else:
        c.hr_approved_by_id = admin.id
        c.hr_approved_at = now
    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.CORRECTION_REJECTED,
        target_table="hr_attendance_corrections",
        target_id=c.id,
        employee_id=c.employee_id,
        payload={"level": body.level, "date": c.attendance_date.isoformat()},
    )
    db.commit()
    db.refresh(c)
    return _to_response(db, c)


@router.delete("/{correction_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_correction(
    correction_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    c = db.query(AttendanceCorrection).filter(AttendanceCorrection.id == correction_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    if c.status != CorrectionStatus.PENDING:
        raise HTTPException(409, "Only PENDING corrections can be deleted")
    c.is_deleted = True
    db.commit()
