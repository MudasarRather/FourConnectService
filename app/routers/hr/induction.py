"""HR Induction — sessions + attendance."""
from __future__ import annotations

from datetime import datetime
from typing import Optional, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.employee import Employee
from app.models.hr.induction import (
    InductionSession, InductionAttendance, InductionType, AttendanceStatus,
)
from app.schemas.hr.induction import (
    InductionSessionCreate, InductionSessionUpdate, InductionSessionResponse,
    InductionAttendanceCreate, InductionAttendanceUpdate, InductionAttendanceResponse,
    InductionBulkInviteBody,
)
from app.utils.dependencies import get_current_superuser
from app.utils.hr.lifecycle_guard import guard_employable


router = APIRouter(prefix="/hr/induction", tags=["HR — Induction"])


def _user_name(db: Session, uid: Optional[UUID]) -> Optional[str]:
    if not uid:
        return None
    r = db.query(User.full_name).filter(User.id == uid).first()
    return r[0] if r else None


def _emp_name(db: Session, eid: Optional[UUID]) -> Optional[str]:
    if not eid:
        return None
    r = (
        db.query(User.full_name)
        .join(Employee, Employee.user_id == User.id)
        .filter(Employee.id == eid)
        .first()
    )
    return r[0] if r else None


def _to_session_response(db: Session, s: InductionSession) -> InductionSessionResponse:
    attendee_count = db.query(func.count(InductionAttendance.id)).filter(
        InductionAttendance.session_id == s.id
    ).scalar() or 0
    confirmed = db.query(func.count(InductionAttendance.id)).filter(
        InductionAttendance.session_id == s.id,
        InductionAttendance.status.in_([AttendanceStatus.CONFIRMED, AttendanceStatus.ATTENDED]),
    ).scalar() or 0
    return InductionSessionResponse(
        id=s.id, name=s.name, session_type=s.session_type,
        scheduled_at=s.scheduled_at, duration_minutes=s.duration_minutes,
        location=s.location, meeting_url=s.meeting_url,
        host_user_id=s.host_user_id, host_name=_user_name(db, s.host_user_id),
        capacity=s.capacity, agenda=s.agenda, materials_url=s.materials_url,
        is_active=s.is_active, attendee_count=int(attendee_count), confirmed_count=int(confirmed),
    )


# ───────────────────────────── Sessions ─────────────────────────────

@router.get("/sessions", response_model=List[InductionSessionResponse])
def list_sessions(
    upcoming_only: bool = False,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(InductionSession).filter(InductionSession.is_deleted == False)  # noqa: E712
    if upcoming_only:
        q = q.filter(InductionSession.scheduled_at >= datetime.utcnow())
    rows = q.order_by(InductionSession.scheduled_at.asc()).all()
    return [_to_session_response(db, s) for s in rows]


@router.post("/sessions", response_model=InductionSessionResponse, status_code=http_status.HTTP_201_CREATED)
def create_session(
    payload: InductionSessionCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    s = InductionSession(**payload.model_dump(), created_by_id=admin.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_session_response(db, s)


@router.patch("/sessions/{session_id}", response_model=InductionSessionResponse)
def update_session(
    session_id: UUID,
    payload: InductionSessionUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    s = db.query(InductionSession).filter(InductionSession.id == session_id, InductionSession.is_deleted == False).first()  # noqa: E712
    if not s:
        raise HTTPException(404, "Session not found")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _to_session_response(db, s)


@router.delete("/sessions/{session_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    s = db.query(InductionSession).filter(InductionSession.id == session_id).first()
    if not s:
        raise HTTPException(404, "Not found")
    s.is_deleted = True
    db.commit()


# ───────────────────────────── Attendance ─────────────────────────────

@router.get("/sessions/{session_id}/attendees", response_model=List[InductionAttendanceResponse])
def list_attendees(
    session_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    rows = (
        db.query(InductionAttendance)
        .filter(InductionAttendance.session_id == session_id)
        .all()
    )
    return [
        InductionAttendanceResponse(
            id=a.id, session_id=a.session_id, employee_id=a.employee_id,
            employee_name=_emp_name(db, a.employee_id),
            process_id=a.process_id, status=a.status, rating=a.rating,
            feedback=a.feedback, rsvp_at=a.rsvp_at, attended_at=a.attended_at,
        ) for a in rows
    ]


@router.post("/sessions/{session_id}/invite", response_model=List[InductionAttendanceResponse])
def bulk_invite(
    session_id: UUID,
    payload: InductionBulkInviteBody,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    if not db.query(InductionSession).filter(InductionSession.id == session_id).first():
        raise HTTPException(404, "Session not found")
    created: List[InductionAttendance] = []
    for emp_id in payload.employee_ids:
        emp = db.query(Employee).filter(Employee.id == emp_id).first()
        guard_employable(emp, "invite this employee to induction")
        existing = db.query(InductionAttendance).filter(
            InductionAttendance.session_id == session_id,
            InductionAttendance.employee_id == emp_id,
        ).first()
        if existing:
            continue
        att = InductionAttendance(
            session_id=session_id,
            employee_id=emp_id,
            status=AttendanceStatus.INVITED,
        )
        db.add(att)
        created.append(att)
    db.commit()
    for a in created:
        db.refresh(a)
    return [
        InductionAttendanceResponse(
            id=a.id, session_id=a.session_id, employee_id=a.employee_id,
            employee_name=_emp_name(db, a.employee_id),
            process_id=a.process_id, status=a.status, rating=a.rating,
            feedback=a.feedback, rsvp_at=a.rsvp_at, attended_at=a.attended_at,
        ) for a in created
    ]


@router.patch("/attendance/{attendance_id}", response_model=InductionAttendanceResponse)
def update_attendance(
    attendance_id: UUID,
    payload: InductionAttendanceUpdate,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    a = db.query(InductionAttendance).filter(InductionAttendance.id == attendance_id).first()
    if not a:
        raise HTTPException(404, "Attendance not found")
    prev = a.status
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(a, k, v)
    if payload.status == AttendanceStatus.CONFIRMED and prev != AttendanceStatus.CONFIRMED:
        a.rsvp_at = datetime.utcnow()
    if payload.status == AttendanceStatus.ATTENDED and prev != AttendanceStatus.ATTENDED:
        a.attended_at = datetime.utcnow()
    db.commit()
    db.refresh(a)
    return InductionAttendanceResponse(
        id=a.id, session_id=a.session_id, employee_id=a.employee_id,
        employee_name=_emp_name(db, a.employee_id),
        process_id=a.process_id, status=a.status, rating=a.rating,
        feedback=a.feedback, rsvp_at=a.rsvp_at, attended_at=a.attended_at,
    )
