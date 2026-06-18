"""HR Training & Development — certification-expiry monitor + background daemon.

``run_certification_expiry`` is an idempotent sweep:
  - expiry in the past            → status EXPIRED (audited once)
  - expiry within 90/60/30 days   → status EXPIRING_SOON; ONE notification per
                                     threshold crossed (deduped on
                                     ``last_notified_window``)
  - a renewable cert in-window with no OPEN renewal assignment → auto-create the
    renewal enrollment and flag the cert PENDING_RENEWAL.

The daemon (``start_training_monitor``) runs this plus the compliance re-assign
engine on its OWN NullPool engine, fully guarded, exactly like the attendance
finalizer. Both are also runnable from ``tasks_cron.py``.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

_thread = None
_engine = None
_Session = None

_RENEWAL_WINDOW_DAYS = 60


def _get_session():
    global _engine, _Session
    if _Session is None:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import NullPool
        from app.config import get_settings
        _engine = create_engine(get_settings().DATABASE_URL, poolclass=NullPool, future=True)
        _Session = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _Session()


def _window_for(days: int) -> Optional[int]:
    if days <= 30:
        return 30
    if days <= 60:
        return 60
    if days <= 90:
        return 90
    return None


def _notify(db: Session, employee_id, *, type_: str, title: str, message: str):
    """Best-effort notification to the employee's user account."""
    try:
        from app.models.notification import Notification
        from app.models.hr.employee import Employee
        row = db.query(Employee.user_id).filter(Employee.id == employee_id).first()
        if not row or not row[0]:
            return
        db.add(Notification(
            user_id=row[0], type=type_, title=title, message=message,
            action_url="/user/self-service/training", is_read=False,
        ))
    except Exception:
        traceback.print_exc()


def run_certification_expiry(db: Session) -> dict:
    """Sweep employee certifications. Idempotent. Returns counts."""
    from app.models.hr.certification import EmployeeCertification, CertificationStatus
    from app.models.hr.training import TrainingAssignment, TrainingAssignmentStatus
    from app.models.hr.training_audit_log import TrainingAuditAction
    from app.utils.hr.training.audit import write_training_audit

    today = date.today()
    expired = 0
    expiring = 0
    renewals = 0

    certs = db.query(EmployeeCertification).filter(
        EmployeeCertification.is_deleted == False,           # noqa: E712
        EmployeeCertification.expiry_date.isnot(None),
        EmployeeCertification.status != CertificationStatus.REVOKED,
    ).all()

    for cert in certs:
        days = (cert.expiry_date - today).days

        if days < 0:
            if cert.status != CertificationStatus.EXPIRED:
                prev = cert.status.value if cert.status else None
                cert.status = CertificationStatus.EXPIRED
                write_training_audit(
                    db, entity_type="CERTIFICATION", entity_id=cert.id,
                    action=TrainingAuditAction.EXPIRE, from_status=prev,
                    to_status=CertificationStatus.EXPIRED.value,
                    note=f"{cert.name} expired on {cert.expiry_date.isoformat()}",
                )
                _notify(db, cert.employee_id, type_="cert_expired",
                        title="Certification expired",
                        message=f"{cert.name} expired on {cert.expiry_date.isoformat()}")
                expired += 1
            continue

        window = _window_for(days)
        if window is not None:
            if cert.status == CertificationStatus.ACTIVE:
                cert.status = CertificationStatus.EXPIRING_SOON
            # Notify once per threshold crossed (dedup on last_notified_window).
            if cert.last_notified_window is None or cert.last_notified_window > window:
                cert.last_notified_window = window
                _notify(db, cert.employee_id, type_="cert_expiring",
                        title="Certification expiring soon",
                        message=f"{cert.name} expires in {days} day(s) on {cert.expiry_date.isoformat()}")
                expiring += 1

            # Auto-renewal: if renewable and inside the renewal window, ensure an
            # open renewal assignment exists. Idempotent (skip if one already does).
            if cert.renewal_training_program_id and days <= _RENEWAL_WINDOW_DAYS:
                open_exists = db.query(TrainingAssignment.id).filter(
                    TrainingAssignment.employee_id == cert.employee_id,
                    TrainingAssignment.program_id == cert.renewal_training_program_id,
                    TrainingAssignment.status.in_(
                        (TrainingAssignmentStatus.NOT_STARTED, TrainingAssignmentStatus.IN_PROGRESS)
                    ),
                ).first()
                if not open_exists:
                    a = TrainingAssignment(
                        program_id=cert.renewal_training_program_id,
                        employee_id=cert.employee_id,
                        assigned_date=today,
                        due_date=cert.expiry_date,
                        status=TrainingAssignmentStatus.NOT_STARTED,
                        enrollment_source="COMPLIANCE",
                        notes=f"Renewal for certification: {cert.name}",
                    )
                    db.add(a)
                    db.flush()
                    cert.status = CertificationStatus.PENDING_RENEWAL
                    write_training_audit(
                        db, entity_type="ASSIGNMENT", entity_id=a.id,
                        action=TrainingAuditAction.RENEW,
                        note=f"Auto renewal enrollment for {cert.name}",
                    )
                    renewals += 1

    db.commit()
    return {"expired": expired, "expiring_notified": expiring, "renewals_created": renewals}


def run_training_maintenance(db: Session) -> dict:
    """Run the full daily Training maintenance: expiry sweep + compliance reassign."""
    out = {}
    try:
        out["expiry"] = run_certification_expiry(db)
    except Exception:
        db.rollback()
        traceback.print_exc()
        out["expiry"] = {"error": True}
    try:
        from app.utils.hr.training.compliance_engine import run_compliance_reassign
        out["compliance"] = run_compliance_reassign(db)
    except Exception:
        db.rollback()
        traceback.print_exc()
        out["compliance"] = {"error": True}
    return out


def _loop(interval_seconds: int):
    time.sleep(min(interval_seconds, 180))  # small initial delay past cold-start
    while True:
        try:
            db = _get_session()
            try:
                run_training_maintenance(db)
            finally:
                db.close()
        except Exception:
            traceback.print_exc()
        time.sleep(interval_seconds)


def start_training_monitor(interval_seconds: int = 6 * 3600):
    """Start the background training monitor (idempotent). Default cadence 6h."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(
        target=_loop, args=(interval_seconds,), daemon=True, name="training-monitor",
    )
    _thread.start()
    return _thread
