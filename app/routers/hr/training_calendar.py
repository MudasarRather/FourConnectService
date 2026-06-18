"""HR Training & Development — unified training calendar (read-only aggregation).

Composes events from enrollment due-dates, certification expiries and compliance
windows into one date-keyed feed. No new tables.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, List, Any, Dict
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.models.hr.training import TrainingProgram, TrainingAssignment, TrainingAssignmentStatus
from app.models.hr.certification import EmployeeCertification, CertificationStatus
from app.utils.hr.training.service import emp_display
from app.utils.dependencies import get_current_superuser

router = APIRouter(prefix="/hr/training", tags=["HR — Training Calendar"])

_OPEN = (TrainingAssignmentStatus.NOT_STARTED, TrainingAssignmentStatus.IN_PROGRESS)


@router.get("/calendar")
def calendar(
    date_from: Optional[date] = Query(None, alias="from"),
    date_to: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_superuser),
):
    today = date.today()
    start = date_from or today.replace(day=1)
    end = date_to or (start + timedelta(days=62))

    events: List[Dict[str, Any]] = []

    # 1) Enrollment due-dates (training to be completed)
    rows = (
        db.query(TrainingAssignment, TrainingProgram)
        .join(TrainingProgram, TrainingProgram.id == TrainingAssignment.program_id)
        .filter(
            TrainingAssignment.due_date.isnot(None),
            TrainingAssignment.due_date >= start,
            TrainingAssignment.due_date <= end,
        )
        .limit(800)
        .all()
    )
    for a, p in rows:
        overdue = a.status in _OPEN and a.due_date < today
        disp = emp_display(db, a.employee_id)
        events.append({
            "date": a.due_date.isoformat(),
            "type": "training_due",
            "title": f"{p.name} due",
            "employee_id": str(a.employee_id),
            "employee_name": disp.get("name"),
            "program_id": str(a.program_id),
            "ref": str(a.id),
            "status": "overdue" if overdue else ("done" if a.status == TrainingAssignmentStatus.COMPLETED else "open"),
        })

    # 2) Certification expiries
    certs = db.query(EmployeeCertification).filter(
        EmployeeCertification.is_deleted == False,  # noqa: E712
        EmployeeCertification.expiry_date.isnot(None),
        EmployeeCertification.expiry_date >= start,
        EmployeeCertification.expiry_date <= end,
        EmployeeCertification.status != CertificationStatus.REVOKED,
    ).limit(800).all()
    for c in certs:
        disp = emp_display(db, c.employee_id)
        days = (c.expiry_date - today).days
        events.append({
            "date": c.expiry_date.isoformat(),
            "type": "cert_expiry",
            "title": f"{c.name} expires",
            "employee_id": str(c.employee_id),
            "employee_name": disp.get("name"),
            "program_id": None,
            "ref": str(c.id),
            "status": "overdue" if days < 0 else ("soon" if days <= 30 else "open"),
        })

    events.sort(key=lambda e: e["date"])
    return {
        "from": start.isoformat(), "to": end.isoformat(),
        "events": events, "total": len(events),
    }
