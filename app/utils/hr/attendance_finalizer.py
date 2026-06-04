"""Shift-end attendance finalizer.

Resolves un-actioned attendance "in real time at shift end": shortly after each
employee's shift ends, their day is stamped with a definitive status
(PRESENT / LATE / HALF_DAY / ABSENT / WEEK_OFF / HOLIDAY / LEAVE / WFH) and a
Loss-of-Pay tag (`lop_days`) for the future payroll module. We CLASSIFY only —
leave balances are never mutated here.

Why a dedicated engine + daemon thread (not APScheduler):
- APScheduler isn't a dependency, and we don't want to add one.
- The main app engine uses `StaticPool` (one shared connection, chosen for
  Python-3.14 stability). A background thread writing through that single
  connection could contend with request handlers, so the finalizer uses its
  OWN `NullPool` engine (a fresh connection per run, closed afterwards).
- The loop is fully wrapped in try/except so a transient DB error can never
  crash the API or the thread.

The same `finalize_due_attendance()` is also callable from `tasks_cron.py` for
an external scheduler, so deployments without the in-process thread still work.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta

_thread = None
_engine = None
_Session = None


def _get_session():
    """Lazily build a dedicated NullPool session factory (separate from the
    app's StaticPool engine) so background writes never share the request
    connection."""
    global _engine, _Session
    if _Session is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        from app.config import get_settings
        _engine = create_engine(get_settings().DATABASE_URL, poolclass=NullPool, future=True)
        _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _Session()


def finalize_due_attendance(db, *, lookback_days: int = 2) -> int:
    """Finalize attendance for fully-elapsed days and for today's shifts that
    have already ended. Idempotent — safe to run repeatedly. Returns the number
    of (employee, day) records finalized today.
    """
    from app.utils.hr.attendance_logic import (
        resolve_shift, mark_absentees, daily_rollup, _combine, IST,
    )
    from app.models.hr.attendance import Attendance, AttendanceSource
    from app.models.hr.employee import Employee, LifecycleState

    now_ist = datetime.now(IST)
    today = now_ist.date()

    # 1) Gap-fill fully-elapsed past days (yesterday, day-before): anyone with no
    #    row gets a definitive status via daily_rollup (ABSENT / WEEK_OFF / …).
    for delta in range(lookback_days, 0, -1):
        d = today - timedelta(days=delta)
        try:
            mark_absentees(db, d)
            db.commit()
        except Exception:
            db.rollback()
            traceback.print_exc()

    # 2) Today: finalize each active employee whose shift has already ended.
    processed = 0
    try:
        emps = (
            db.query(Employee)
            .filter(Employee.is_deleted == False,  # noqa: E712
                    Employee.lifecycle_state == LifecycleState.ACTIVE)
            .all()
        )
    except Exception:
        db.rollback()
        return 0

    for emp in emps:
        try:
            shift = resolve_shift(db, emp.id, today)
            if not shift:
                continue
            end_dt = _combine(today, shift.end_time)
            if shift.end_time <= shift.start_time:   # overnight shift ends next day
                end_dt = end_dt + timedelta(days=1)
            # Wait until the shift has genuinely ended (+5 min settle window).
            if now_ist < end_dt + timedelta(minutes=5):
                continue
            existing = (
                db.query(Attendance)
                .filter(Attendance.employee_id == emp.id, Attendance.date == today)
                .first()
            )
            if existing and existing.is_locked:
                continue  # admin-locked rows are authoritative
            daily_rollup(db, emp.id, today, source=AttendanceSource.SYSTEM)
            db.commit()
            processed += 1
        except Exception:
            db.rollback()
            traceback.print_exc()

    return processed


def _loop(interval_seconds: int):
    # Small initial delay so we don't pile onto cold-start / create_all.
    time.sleep(min(interval_seconds, 120))
    while True:
        try:
            db = _get_session()
            try:
                finalize_due_attendance(db)
            finally:
                db.close()
        except Exception:
            traceback.print_exc()
        time.sleep(interval_seconds)


def start_finalizer(interval_seconds: int = 900):
    """Start the background finalizer thread (idempotent). Default cadence is
    15 minutes → a day is finalized within ~15 min of the shift ending."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(
        target=_loop, args=(interval_seconds,), daemon=True, name="att-finalizer",
    )
    _thread.start()
    return _thread
