"""Travel lifecycle automation — date-driven START / COMPLETE transitions.

Single source of truth for the two lifecycle transitions, shared by:
  • the admin route handlers (manual Start / Complete), and
  • a background daemon (+ ``tasks_cron.py``) that fires them automatically on
    the trip's own departure / return dates.

Workflow (no loophole):
  APPROVED ──(on/after departure_date)──► IN_PROGRESS ──(after return_date)──► COMPLETED
  • A trip can't be started before its departure date.
  • A trip can't be completed before its return date, and only once it's started
    (so attendance is always synced first).
  • If nobody acts manually, the daemon does it on schedule.

Like the attendance finalizer, the daemon uses its OWN NullPool engine so its
writes never contend with the app's StaticPool request connection, and the loop
is fully guarded so a transient DB error can never crash the API or the thread.
The same ``run_travel_auto_transitions()`` is callable from ``tasks_cron.py`` for
an external scheduler, so deployments without the in-process thread still work.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.hr.travel_request import TravelRequest
from app.models.hr.travel_settlement import TravelSettlement
from app.models.hr.travel_type import (
    TravelRequestStatus, TravelSettlementStatus, TravelAuditAction,
)
from app.utils.hr.travel.chain import assert_transition
from app.utils.hr.travel.attendance_sync import mark_on_duty
from app.utils.hr.travel.settlement import reconcile
from app.utils.hr.travel.service import write_travel_audit, generate_settlement_number, trav_today


# ─────────────────────────── shared transitions ───────────────────────────
# These mutate + audit but DO NOT commit — the caller (route handler or the
# auto-loop) owns the transaction boundary.

def execute_travel(db: Session, req: TravelRequest, *, actor_id=None, sync_attendance: bool = True,
                   auto: bool = False) -> int:
    """APPROVED → IN_PROGRESS. Marks the tour's attendance ON_DUTY. Returns days synced."""
    assert_transition(req.status, TravelRequestStatus.IN_PROGRESS)
    req.status = TravelRequestStatus.IN_PROGRESS
    req.executed_at = datetime.now(timezone.utc)
    synced = mark_on_duty(db, req, actor_id=actor_id) if sync_attendance else 0
    prefix = "Auto-started on departure date" if auto else "Travel started"
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.EXECUTE, actor_id=actor_id,
                       to_status=req.status.value, note=f"{prefix} · {synced} attendance day(s) marked ON_DUTY")
    return synced


def complete_travel(db: Session, req: TravelRequest, *, actor_id=None, auto: bool = False) -> None:
    """IN_PROGRESS → COMPLETED. Opens a DRAFT settlement so expenses can be filed."""
    assert_transition(req.status, TravelRequestStatus.COMPLETED)
    req.status = TravelRequestStatus.COMPLETED
    req.completed_at = datetime.now(timezone.utc)
    existing = db.query(TravelSettlement).filter(
        TravelSettlement.travel_request_id == req.id, TravelSettlement.is_deleted == False).first()  # noqa: E712
    if not existing:
        s = TravelSettlement(
            settlement_number=generate_settlement_number(db), travel_request_id=req.id,
            employee_id=req.employee_id, expense_lines=[], currency=req.currency,
            status=TravelSettlementStatus.DRAFT, created_by_id=actor_id)
        db.add(s)
        db.flush()
        reconcile(db, s)
    write_travel_audit(db, entity_type="REQUEST", entity_id=req.id, travel_request_id=req.id,
                       action=TravelAuditAction.COMPLETE, actor_id=actor_id,
                       to_status=req.status.value, note="Auto-completed after return date" if auto else None)


# ─────────────────────────── the daily sweep ───────────────────────────

def run_travel_auto_transitions(db: Session) -> dict:
    """Auto-start tours whose departure has arrived and auto-complete tours whose
    return date has passed. Idempotent + per-row guarded. Returns counts."""
    today = trav_today()
    started = completed = 0

    # 1) Auto-start: APPROVED whose departure date is today or earlier.
    to_start = db.query(TravelRequest).filter(
        TravelRequest.is_deleted == False,  # noqa: E712
        TravelRequest.status == TravelRequestStatus.APPROVED,
        TravelRequest.departure_date <= today,
    ).all()
    for req in to_start:
        try:
            execute_travel(db, req, actor_id=None, sync_attendance=True, auto=True)
            db.commit()
            started += 1
        except Exception:
            db.rollback()
            traceback.print_exc()

    # 2) Auto-complete: IN_PROGRESS whose return date has fully passed (day after).
    to_complete = db.query(TravelRequest).filter(
        TravelRequest.is_deleted == False,  # noqa: E712
        TravelRequest.status == TravelRequestStatus.IN_PROGRESS,
        TravelRequest.return_date < today,
    ).all()
    for req in to_complete:
        try:
            complete_travel(db, req, actor_id=None, auto=True)
            db.commit()
            completed += 1
        except Exception:
            db.rollback()
            traceback.print_exc()

    return {"started": started, "completed": completed}


# ─────────────────────────── background daemon ───────────────────────────
_thread = None
_engine = None
_Session = None


def _get_session():
    """Dedicated NullPool session (separate from the app's StaticPool engine) so
    background writes never share the request connection."""
    global _engine, _Session
    if _Session is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        from app.config import get_settings
        _engine = create_engine(get_settings().DATABASE_URL, poolclass=NullPool, future=True)
        _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _Session()


def _loop(interval_seconds: int):
    time.sleep(min(interval_seconds, 120))   # small initial delay past cold-start
    while True:
        try:
            db = _get_session()
            try:
                run_travel_auto_transitions(db)
            finally:
                db.close()
        except Exception:
            traceback.print_exc()
        time.sleep(interval_seconds)


def start_travel_scheduler(interval_seconds: int = 3600):
    """Start the travel auto-transition daemon (idempotent). Hourly cadence — a
    trip starts within ~1h of its departure date and completes within ~1h of the
    day after its return date."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_loop, args=(interval_seconds,), daemon=True, name="travel-scheduler")
    _thread.start()
    return _thread
