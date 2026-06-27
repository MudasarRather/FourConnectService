"""HR Notification — daily scheduled scans for TIME-BASED events.

These have no single action to hook, so a daily sweep raises them:
  BIRTHDAY · WORK_ANNIVERSARY · PROBATION_ENDING · CONTRACT_EXPIRY ·
  ASSET_RETURN_DUE · ATTENDANCE_MISSING

Every notification is funnelled through ``app.utils.hr.notify.dispatch`` so the
configurable NotificationRule matrix governs delivery (in-app today; other
channels when their transport lands). Each scan is idempotent for the day via
``already_notified_today`` so re-running the cron never double-pings.

CERTIFICATION_EXPIRY is intentionally NOT here — it is already produced by
``app.utils.hr.training.expiry_monitor.run_certification_expiry`` (deduped on
``last_notified_window``); duplicating it would double-notify.

Run from ``tasks_cron.py`` → ``run_notification_scans(db)``.
"""
from __future__ import annotations

import calendar
import traceback
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.utils.hr.notify import dispatch, already_notified_today

# Lifecycle states that should still receive HR notifications.
_LIVE_STATES = ("ACTIVE", "ON_PROBATION", "ON_NOTICE")

# How many days before a deadline to nudge (the daily run fires once per offset).
_ENDING_OFFSETS = (30, 7, 1)
_ASSET_OFFSETS = (7, 3, 1, 0)


def _add_months(d: date, months: int) -> date:
    """date + N calendar months, clamped to the month's last day (no dateutil dep)."""
    m = d.month - 1 + int(months or 0)
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _live_employees(db: Session):
    from app.models.hr.employee import Employee, LifecycleState
    states = [LifecycleState[s] for s in _LIVE_STATES if hasattr(LifecycleState, s)]
    return (db.query(Employee)
            .filter(Employee.is_deleted == False,                 # noqa: E712
                    Employee.user_id.isnot(None),
                    Employee.lifecycle_state.in_(states))
            .all())


def scan_birthdays(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    n = 0
    for e in _live_employees(db):
        if not e.dob or e.dob.month != today.month or e.dob.day != today.day:
            continue
        if already_notified_today(db, e.user_id, "BIRTHDAY", today):
            continue
        dispatch(db, "BIRTHDAY", e.user_id, context={
            "title": "Happy Birthday! 🎉",
            "message": "Wishing you a wonderful birthday — from everyone at the company.",
            "action_url": "/user/self-service/profile",
        })
        n += 1
    db.commit()
    return n


def scan_anniversaries(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    n = 0
    for e in _live_employees(db):
        j = e.joining_date
        if not j or j.month != today.month or j.day != today.day or j.year >= today.year:
            continue
        years = today.year - j.year
        if already_notified_today(db, e.user_id, "WORK_ANNIVERSARY", today):
            continue
        dispatch(db, "WORK_ANNIVERSARY", e.user_id, context={
            "title": f"{years} year work anniversary 🎊",
            "message": f"Congratulations on completing {years} year{'s' if years != 1 else ''} with us!",
            "action_url": "/user/self-service/profile",
        })
        n += 1
    db.commit()
    return n


def scan_probation_ending(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    from app.models.hr.employee import LifecycleState
    n = 0
    for e in _live_employees(db):
        if e.lifecycle_state != LifecycleState.ON_PROBATION or not e.joining_date:
            continue
        end = _add_months(e.joining_date, e.probation_months or 6)
        days = (end - today).days
        if days not in _ENDING_OFFSETS:
            continue
        if already_notified_today(db, e.user_id, "PROBATION_ENDING", today):
            continue
        dispatch(db, "PROBATION_ENDING", e.user_id, context={
            "title": "Probation ending soon",
            "message": f"Your probation period ends on {end.isoformat()} ({days} day{'s' if days != 1 else ''} away).",
            "action_url": "/user/self-service/profile",
        })
        n += 1
    db.commit()
    return n


def scan_contract_expiry(db: Session, today: date | None = None) -> int:
    """Fixed-term staff whose ``contract_end_date`` is approaching.

    Driven by the dedicated ``Employee.contract_end_date`` column (set in the
    Add/Edit employee forms, shown for contract staff). Fires once per offset.
    """
    today = today or date.today()
    n = 0
    for e in _live_employees(db):
        end = getattr(e, "contract_end_date", None)
        if not end:
            continue
        days = (end - today).days
        if days not in _ENDING_OFFSETS:
            continue
        if already_notified_today(db, e.user_id, "CONTRACT_EXPIRY", today):
            continue
        dispatch(db, "CONTRACT_EXPIRY", e.user_id, context={
            "title": "Contract ending soon",
            "message": f"Your contract is set to end on {end.isoformat()} ({days} day{'s' if days != 1 else ''} away).",
            "action_url": "/user/self-service/profile",
        })
        n += 1
    db.commit()
    return n


def scan_asset_return_due(db: Session, today: date | None = None) -> int:
    today = today or date.today()
    from app.models.hr.asset import AssetAllocation, AllocationStatus
    from app.models.hr.employee import Employee
    rows = (db.query(AssetAllocation)
            .filter(AssetAllocation.status == AllocationStatus.ALLOCATED,
                    AssetAllocation.expected_return_date.isnot(None))
            .all())
    # Aggregate per employee so a person with 3 due assets gets ONE nudge.
    by_emp: dict = {}
    for a in rows:
        days = (a.expected_return_date - today).days
        if days in _ASSET_OFFSETS:
            by_emp.setdefault(a.employee_id, []).append(a)
    n = 0
    for emp_id, allocs in by_emp.items():
        uid = db.query(Employee.user_id).filter(Employee.id == emp_id).scalar()
        if not uid or already_notified_today(db, uid, "ASSET_RETURN_DUE", today):
            continue
        cnt = len(allocs)
        dispatch(db, "ASSET_RETURN_DUE", uid, context={
            "title": "Asset return due",
            "message": f"You have {cnt} allocated asset{'s' if cnt != 1 else ''} due for return soon.",
            "action_url": "/user/self-service/assets",
        })
        n += 1
    db.commit()
    return n


def scan_attendance_missing(db: Session, today: date | None = None) -> int:
    """Yesterday's genuinely-missing attendance (ABSENT after the finalizer ran)."""
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    from app.models.hr.attendance import Attendance, AttendanceStatus
    from app.models.hr.employee import Employee
    rows = (db.query(Attendance)
            .filter(Attendance.date == yesterday,
                    Attendance.status == AttendanceStatus.ABSENT,
                    Attendance.is_deleted == False)               # noqa: E712
            .all())
    n = 0
    for att in rows:
        uid = db.query(Employee.user_id).filter(Employee.id == att.employee_id).scalar()
        if not uid or already_notified_today(db, uid, "ATTENDANCE_MISSING", today):
            continue
        dispatch(db, "ATTENDANCE_MISSING", uid, context={
            "title": "Attendance not recorded",
            "message": f"No attendance was recorded for you on {yesterday.isoformat()}. Please regularise it if this is an error.",
            "action_url": "/user/self-service/attendance",
        })
        n += 1
    db.commit()
    return n


def run_notification_scans(db: Session) -> dict:
    """Run every time-based notification scan. Each is independently guarded so one
    failure never blocks the others."""
    out: dict = {}
    for key, fn in (
        ("birthdays", scan_birthdays),
        ("anniversaries", scan_anniversaries),
        ("probation_ending", scan_probation_ending),
        ("contract_expiry", scan_contract_expiry),
        ("asset_return_due", scan_asset_return_due),
        ("attendance_missing", scan_attendance_missing),
    ):
        try:
            out[key] = fn(db)
        except Exception:
            db.rollback()
            traceback.print_exc()
            out[key] = {"error": True}
    return out
