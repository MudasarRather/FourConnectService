"""HR Travel ↔ Attendance integration.

When a travel request is executed, mark the traveller's attendance ON_DUTY for
the tour dates so they don't show ABSENT and still earn shift-based premiums
(ON_DUTY is in payroll's ``_WORKED_STATUSES``). Mirrors the attendance-recompute
caution: we only touch the specific tour-date rows (never a blanket backfill),
skip payroll-locked rows, and never clobber an existing LEAVE / HOLIDAY / WEEK_OFF
classification. Rows we create carry a ``[TRAVEL:<ref>]`` remark so a later cancel
can cleanly unwind exactly what travel added.
"""
from __future__ import annotations

from datetime import timedelta, date as _date

from sqlalchemy.orm import Session

from app.models.hr.travel_request import TravelRequest
from app.models.hr.attendance import Attendance, AttendanceStatus, AttendanceSource

# Statuses travel must NOT overwrite (they are authoritative leave / calendar marks).
_PROTECTED = (
    AttendanceStatus.LEAVE, AttendanceStatus.HOLIDAY,
    AttendanceStatus.WEEK_OFF, AttendanceStatus.LWP,
)


def _marker(req: TravelRequest) -> str:
    return f"[TRAVEL:{req.travel_reference_number}]"


def _date_range(start: _date, end: _date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def mark_on_duty(db: Session, req: TravelRequest, *, actor_id=None) -> int:
    """Upsert ON_DUTY attendance for [departure_date, return_date]. Returns the
    number of rows created/updated."""
    if not req.departure_date or not req.return_date:
        return 0
    marker = _marker(req)
    touched = 0
    for d in _date_range(req.departure_date, req.return_date):
        row = db.query(Attendance).filter(
            Attendance.employee_id == req.employee_id, Attendance.date == d,
            Attendance.is_deleted == False,  # noqa: E712
        ).first()
        if row:
            if row.is_locked or row.status in _PROTECTED:
                continue
            row.status = AttendanceStatus.ON_DUTY
            row.source = AttendanceSource.SYSTEM
            row.remarks = f"{marker} On official travel"
            row.last_updated_by_id = actor_id
            touched += 1
        else:
            db.add(Attendance(
                employee_id=req.employee_id, date=d, status=AttendanceStatus.ON_DUTY,
                source=AttendanceSource.SYSTEM, remarks=f"{marker} On official travel",
                created_by_id=actor_id,
            ))
            touched += 1
    req.attendance_synced = True
    return touched


def unmark_on_duty(db: Session, req: TravelRequest) -> int:
    """Undo travel-created ON_DUTY rows (identified by the [TRAVEL:<ref>] marker).
    Newly-created rows are soft-deleted; pre-existing rows we flipped revert to
    ABSENT so the daily rollup can re-classify them. Locked rows are left alone."""
    if not req.attendance_synced:
        return 0
    marker = _marker(req)
    rows = db.query(Attendance).filter(
        Attendance.employee_id == req.employee_id,
        Attendance.status == AttendanceStatus.ON_DUTY,
        Attendance.remarks.like(f"{marker}%"),
        Attendance.is_deleted == False,  # noqa: E712
    ).all()
    n = 0
    for row in rows:
        if row.is_locked:
            continue
        row.status = AttendanceStatus.ABSENT
        row.remarks = None
        n += 1
    req.attendance_synced = False
    return n
