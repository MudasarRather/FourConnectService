"""HR Half-Day requests.

Two creation paths:
    * ``POST /me``  → employee request (status=PENDING)
    * ``POST /``    → admin manual tag (status=APPROVED, ``is_admin_override``)

Approval/rejection mirrors WFH. When a row lands in APPROVED state we re-run
``daily_rollup`` for the half-day date so the Attendance row's status flips
to HALF_DAY synchronously — no cron lag.

All admin endpoints require superuser; user endpoints accept any active user.
"""
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
from app.models.hr.department import Department
from app.models.hr.half_day_request import HalfDayRequest, HalfDayStatus, HalfDayWhich, HalfDayReason
from app.models.hr.attendance_log import AttendanceLogAction
from app.models.hr.holiday import Holiday, HolidayType
from app.schemas.hr.attendance import (
    HalfDayCreate, HalfDayAdminCreate, HalfDayDecideBody,
    HalfDayResponse, HalfDayListResponse, HalfDayStats,
)
from app.utils.dependencies import get_current_user, get_current_superuser
from app.utils.hr.attendance_logic import daily_rollup, log, resolve_shift


router = APIRouter(prefix="/hr/half-day", tags=["HR — Half-Day"])


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _to_response(db: Session, h: HalfDayRequest) -> HalfDayResponse:
    snap = (
        db.query(Employee.employee_id, User.full_name, Department.name.label("dept"))
        .join(User, User.id == Employee.user_id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .filter(Employee.id == h.employee_id)
        .first()
    )
    approver = None
    if h.manager_approved_by_id:
        approver_row = (
            db.query(User.full_name)
            .filter(User.id == h.manager_approved_by_id)
            .first()
        )
        approver = approver_row[0] if approver_row else None

    return HalfDayResponse(
        id=h.id, employee_id=h.employee_id,
        employee_name=snap.full_name if snap else None,
        employee_code=snap.employee_id if snap else None,
        department=snap.dept if snap else None,
        half_day_date=h.half_day_date,
        which_half=h.which_half,
        reason_type=h.reason_type,
        reason=h.reason,
        status=h.status,
        manager_approved_by_id=h.manager_approved_by_id,
        manager_approved_by_name=approver,
        manager_approved_at=h.manager_approved_at,
        decision_notes=h.decision_notes,
        is_admin_override=bool(h.is_admin_override),
        created_at=h.created_at,
    )


def _resolve_self(db: Session, user: User) -> Employee:
    emp = db.query(Employee).filter(
        Employee.user_id == user.id,
        Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "No employee profile linked to your account")
    return emp


def _check_no_duplicate(db: Session, employee_id: UUID, half_day_date: date, exclude_id: Optional[UUID] = None) -> None:
    """Block second pending/approved request for the same employee+date."""
    q = db.query(HalfDayRequest).filter(
        HalfDayRequest.employee_id == employee_id,
        HalfDayRequest.half_day_date == half_day_date,
        HalfDayRequest.is_deleted == False,  # noqa: E712
        HalfDayRequest.status.in_([HalfDayStatus.PENDING, HalfDayStatus.APPROVED]),
    )
    if exclude_id:
        q = q.filter(HalfDayRequest.id != exclude_id)
    if q.first():
        raise HTTPException(409, "A half-day request already exists for this date")


def _check_not_off_day(db: Session, employee_id: UUID, half_day_date: date) -> None:
    """Reject half-day requests on weekly off days or active holidays.

    There's nothing to "take half off" when the entire day is already off, so
    the request would be a no-op at best and a payroll mis-tag at worst.
    Raises 422 with a descriptive message naming the conflict so the UI can
    surface it cleanly.
    """
    # 1) Weekly off: check the employee's resolved shift for this date
    shift = resolve_shift(db, employee_id, half_day_date)
    if shift and half_day_date.weekday() in (shift.weekly_off_days or []):
        weekday_name = half_day_date.strftime("%A")
        raise HTTPException(
            422,
            f"{weekday_name} is a weekly off for your shift — no half-day needed.",
        )

    # 2) Holiday: any active, non-RESTRICTED holiday for that date that covers
    #    the employee's work location (or is global).
    emp = db.query(Employee).filter(Employee.id == employee_id).first()
    holidays = (
        db.query(Holiday)
        .filter(
            Holiday.date == half_day_date,
            Holiday.is_active == True,        # noqa: E712
            Holiday.is_deleted == False,      # noqa: E712
            Holiday.holiday_type != HolidayType.RESTRICTED,
        )
        .all()
    )
    for h in holidays:
        if h.location_id is None or (emp and emp.work_location_id == h.location_id):
            raise HTTPException(
                422,
                f"{half_day_date.strftime('%d %b %Y')} is a company holiday ({h.name}) — no half-day needed.",
            )


# ──────────────────────────────────────────────────────────────────────────
# Admin — list / stats
# ──────────────────────────────────────────────────────────────────────────


@router.get("/", response_model=HalfDayListResponse)
def list_half_day(
    status_filter: Optional[HalfDayStatus] = Query(None, alias="status"),
    employee_id: Optional[UUID] = None,
    department_id: Optional[UUID] = None,
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    q = db.query(HalfDayRequest).filter(HalfDayRequest.is_deleted == False)  # noqa: E712
    if status_filter:
        q = q.filter(HalfDayRequest.status == status_filter)
    if employee_id:
        q = q.filter(HalfDayRequest.employee_id == employee_id)
    if department_id:
        q = q.join(Employee, Employee.id == HalfDayRequest.employee_id).filter(
            Employee.department_id == department_id
        )
    if from_:
        q = q.filter(HalfDayRequest.half_day_date >= from_)
    if to:
        q = q.filter(HalfDayRequest.half_day_date <= to)

    total = q.count()
    rows = (
        q.order_by(HalfDayRequest.half_day_date.desc(), HalfDayRequest.created_at.desc())
         .offset((page - 1) * limit).limit(limit).all()
    )
    return HalfDayListResponse(
        items=[_to_response(db, r) for r in rows],
        total=total, page=page, limit=limit,
        total_pages=ceil(total / limit) if limit else 1,
    )


@router.get("/stats", response_model=HalfDayStats)
def half_day_stats(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    base = db.query(HalfDayRequest).filter(HalfDayRequest.is_deleted == False)  # noqa: E712
    today = date.today()
    return HalfDayStats(
        pending=base.filter(HalfDayRequest.status == HalfDayStatus.PENDING).count(),
        approved=base.filter(HalfDayRequest.status == HalfDayStatus.APPROVED).count(),
        rejected=base.filter(HalfDayRequest.status == HalfDayStatus.REJECTED).count(),
        upcoming=base.filter(
            HalfDayRequest.status == HalfDayStatus.APPROVED,
            HalfDayRequest.half_day_date >= today,
        ).count(),
    )


# ──────────────────────────────────────────────────────────────────────────
# User — self-service
# ──────────────────────────────────────────────────────────────────────────


@router.get("/me", response_model=HalfDayListResponse)
def my_half_day(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self(db, user)
    rows = (
        db.query(HalfDayRequest)
        .filter(
            HalfDayRequest.employee_id == emp.id,
            HalfDayRequest.is_deleted == False,  # noqa: E712
        )
        .order_by(HalfDayRequest.half_day_date.desc())
        .all()
    )
    return HalfDayListResponse(
        items=[_to_response(db, r) for r in rows],
        total=len(rows), page=1, limit=max(1, len(rows)), total_pages=1,
    )


@router.post("/me", response_model=HalfDayResponse, status_code=http_status.HTTP_201_CREATED)
def create_my_half_day(
    payload: HalfDayCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    emp = _resolve_self(db, user)
    # Past-date requests need admin override flow, not user-initiated
    if payload.half_day_date < date.today():
        raise HTTPException(422, "Half-day requests must be for today or a future date")
    _check_no_duplicate(db, emp.id, payload.half_day_date)
    _check_not_off_day(db, emp.id, payload.half_day_date)

    h = HalfDayRequest(
        employee_id=emp.id,
        half_day_date=payload.half_day_date,
        which_half=payload.which_half,
        reason_type=payload.reason_type,
        reason=payload.reason,
        status=HalfDayStatus.PENDING,
    )
    db.add(h)
    db.commit()
    db.refresh(h)
    # Audit log after the row is persisted — keeps the refresh on a clean
    # session state (mirroring the working WFH pattern). The log is its own
    # commit so its failure can't roll back the user's request.
    try:
        log(
            db,
            actor_id=user.id,
            action=AttendanceLogAction.HALF_DAY_REQUESTED,
            target_table="hr_half_day_requests",
            target_id=h.id,
            employee_id=emp.id,
            payload={
                "date": payload.half_day_date.isoformat(),
                "which_half": payload.which_half.value,
                "reason_type": payload.reason_type.value,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, h)


@router.delete("/me/{half_day_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def cancel_my_half_day(
    half_day_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """User cancels their own PENDING request. Approved requests must be
    cancelled by an admin (because they've already affected attendance)."""
    emp = _resolve_self(db, user)
    h = db.query(HalfDayRequest).filter(
        HalfDayRequest.id == half_day_id,
        HalfDayRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not h:
        raise HTTPException(404, "Half-day request not found")
    if h.employee_id != emp.id:
        raise HTTPException(403, "Cannot cancel another employee's request")
    if h.status != HalfDayStatus.PENDING:
        raise HTTPException(409, f"Only PENDING requests can be cancelled; this is {h.status.value}")
    h.status = HalfDayStatus.CANCELLED
    h.is_deleted = True
    db.commit()


# ──────────────────────────────────────────────────────────────────────────
# Admin — single fetch / approve / reject / manual tag
# ──────────────────────────────────────────────────────────────────────────


@router.get("/{half_day_id}", response_model=HalfDayResponse)
def get_half_day(
    half_day_id: UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    h = db.query(HalfDayRequest).filter(
        HalfDayRequest.id == half_day_id,
        HalfDayRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not h:
        raise HTTPException(404, "Half-day request not found")
    return _to_response(db, h)


@router.post("/", response_model=HalfDayResponse, status_code=http_status.HTTP_201_CREATED)
def admin_override(
    payload: HalfDayAdminCreate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Admin manually tags an employee's day as HALF_DAY. Skips PENDING."""
    emp = db.query(Employee).filter(
        Employee.id == payload.employee_id,
        Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "Employee not found")
    _check_no_duplicate(db, payload.employee_id, payload.half_day_date)

    h = HalfDayRequest(
        employee_id=payload.employee_id,
        half_day_date=payload.half_day_date,
        which_half=payload.which_half,
        reason_type=payload.reason_type,
        reason=payload.reason,
        status=HalfDayStatus.APPROVED,
        manager_approved_by_id=admin.id,
        manager_approved_at=datetime.now(timezone.utc),
        decision_notes="Admin manual override",
        is_admin_override=True,
    )
    db.add(h)
    db.commit()        # commit BEFORE the rollup so the inserted row is visible
    db.refresh(h)      # safe — clean session state with no pending audit
    # Audit + recompute attendance in a follow-up transaction; isolating
    # them keeps the create txn small and lets the rollup query see the
    # committed APPROVED row.
    try:
        log(
            db,
            actor_id=admin.id,
            action=AttendanceLogAction.HALF_DAY_OVERRIDE,
            target_table="hr_half_day_requests",
            target_id=h.id,
            employee_id=payload.employee_id,
            payload={
                "date": payload.half_day_date.isoformat(),
                "which_half": payload.which_half.value,
                "reason": payload.reason,
            },
        )
        daily_rollup(db, payload.employee_id, payload.half_day_date, actor_id=admin.id)
        db.commit()
    except Exception:
        db.rollback()  # half-day row still stands; rollup will run again on next punch
    return _to_response(db, h)


@router.patch("/{half_day_id}/approve", response_model=HalfDayResponse)
def approve_half_day(
    half_day_id: UUID,
    body: HalfDayDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    h = db.query(HalfDayRequest).filter(
        HalfDayRequest.id == half_day_id,
        HalfDayRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not h:
        raise HTTPException(404, "Half-day request not found")
    if h.status != HalfDayStatus.PENDING:
        raise HTTPException(409, f"Half-day request already {h.status.value}")

    h.status = HalfDayStatus.APPROVED
    h.manager_approved_by_id = admin.id
    h.manager_approved_at = datetime.now(timezone.utc)
    h.decision_notes = body.notes
    db.commit()
    db.refresh(h)
    # Run the audit + attendance recompute in a separate transaction. The
    # rollup's read of HalfDayRequest sees the APPROVED row because it's
    # already committed; the audit log is best-effort so a failure here
    # never rolls back the approval itself.
    try:
        log(
            db,
            actor_id=admin.id,
            action=AttendanceLogAction.HALF_DAY_APPROVED,
            target_table="hr_half_day_requests",
            target_id=h.id,
            employee_id=h.employee_id,
            payload={"date": h.half_day_date.isoformat(), "notes": body.notes},
        )
        daily_rollup(db, h.employee_id, h.half_day_date, actor_id=admin.id)
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, h)


@router.patch("/{half_day_id}/reject", response_model=HalfDayResponse)
def reject_half_day(
    half_day_id: UUID,
    body: HalfDayDecideBody,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    h = db.query(HalfDayRequest).filter(
        HalfDayRequest.id == half_day_id,
        HalfDayRequest.is_deleted == False,  # noqa: E712
    ).first()
    if not h:
        raise HTTPException(404, "Half-day request not found")
    if h.status != HalfDayStatus.PENDING:
        raise HTTPException(409, f"Half-day request already {h.status.value}")

    h.status = HalfDayStatus.REJECTED
    h.manager_approved_by_id = admin.id
    h.manager_approved_at = datetime.now(timezone.utc)
    h.decision_notes = body.notes
    db.commit()
    db.refresh(h)
    try:
        log(
            db,
            actor_id=admin.id,
            action=AttendanceLogAction.HALF_DAY_REJECTED,
            target_table="hr_half_day_requests",
            target_id=h.id,
            employee_id=h.employee_id,
            payload={"date": h.half_day_date.isoformat(), "notes": body.notes},
        )
        db.commit()
    except Exception:
        db.rollback()
    return _to_response(db, h)


@router.delete("/{half_day_id}", status_code=http_status.HTTP_204_NO_CONTENT)
def admin_delete_half_day(
    half_day_id: UUID,
    reason: Optional[str] = Query(None, max_length=400),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """Admin soft-delete. If the row was APPROVED, also re-run the rollup so
    the attendance status reverts to whatever the punches imply.

    ``reason`` is appended to ``decision_notes`` so the revert is traceable in
    the half-day audit view, and a HALF_DAY_OVERRIDE log row is emitted.
    """
    h = db.query(HalfDayRequest).filter(HalfDayRequest.id == half_day_id).first()
    if not h:
        raise HTTPException(404, "Half-day request not found")
    was_approved = h.status == HalfDayStatus.APPROVED
    employee_id = h.employee_id
    half_day_date = h.half_day_date

    h.is_deleted = True
    h.status = HalfDayStatus.CANCELLED
    cleaned = (reason or "").strip()
    if cleaned:
        prefix = "[REVERTED] "
        h.decision_notes = f"{prefix}{cleaned}" if not h.decision_notes else f"{h.decision_notes}\n{prefix}{cleaned}"
    db.commit()

    log(
        db,
        actor_id=admin.id,
        action=AttendanceLogAction.HALF_DAY_OVERRIDE,
        target_table="hr_half_day_requests",
        target_id=h.id,
        employee_id=employee_id,
        payload={
            "event": "revert",
            "half_day_date": half_day_date.isoformat(),
            "which_half": h.which_half.value if hasattr(h.which_half, "value") else str(h.which_half),
            "was_approved": was_approved,
            "reason": cleaned or None,
        },
    )
    db.commit()

    if was_approved:
        daily_rollup(db, employee_id, half_day_date, actor_id=admin.id)
        db.commit()
