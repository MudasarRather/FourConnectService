"""HR Performance — Performance Improvement Plans (admin / superuser).

A time-boxed plan with measurable objectives, scheduled check-ins, support, and
a final outcome.  Workflow: DRAFT → ACTIVE → (EXTENDED) → SUCCESSFUL|UNSUCCESSFUL
(+ CANCELLED).

Distinct prefix /hr/performance-pip.
"""
from __future__ import annotations

from datetime import datetime, date, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.utils.dependencies import get_current_superuser
from app.models.hr.employee import Employee
from app.models.hr.performance_pip import PerformancePip, PipStatus
from app.schemas.hr.performance_pip import PipCreate, PipUpdate, PipCheckIn, PipTransition
from app.utils.hr.performance_service import serialize_pip, user_name

router = APIRouter(prefix="/hr/performance-pip", tags=["HR — Performance PIP"])

P = PipStatus
ALLOWED = {
    P.DRAFT.value: {P.ACTIVE.value, P.CANCELLED.value},
    P.ACTIVE.value: {P.EXTENDED.value, P.SUCCESSFUL.value, P.UNSUCCESSFUL.value, P.CANCELLED.value},
    P.EXTENDED.value: {P.SUCCESSFUL.value, P.UNSUCCESSFUL.value, P.CANCELLED.value, P.ACTIVE.value},
    P.SUCCESSFUL.value: {P.ACTIVE.value},
    P.UNSUCCESSFUL.value: {P.ACTIVE.value},
    P.CANCELLED.value: {P.DRAFT.value, P.ACTIVE.value},
}


def _now():
    return datetime.now(timezone.utc)


def _to_dt(d: Optional[date]):
    return datetime.combine(d, datetime.min.time()) if d else None


def _load(db: Session, pip_id: UUID) -> PerformancePip:
    p = db.query(PerformancePip).filter(
        PerformancePip.id == pip_id, PerformancePip.is_deleted == False,  # noqa: E712
    ).first()
    if not p:
        raise HTTPException(404, "PIP not found")
    return p


@router.get("/stats")
def pip_stats(db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    base = db.query(PerformancePip).filter(PerformancePip.is_deleted == False)  # noqa: E712
    by_status = {}
    for st, cnt in base.with_entities(PerformancePip.status, func.count(PerformancePip.id)).group_by(PerformancePip.status).all():
        by_status[st] = cnt
    active = by_status.get(P.ACTIVE.value, 0) + by_status.get(P.EXTENDED.value, 0)
    due_soon = base.filter(
        PerformancePip.status.in_([P.ACTIVE.value, P.EXTENDED.value]),
        PerformancePip.end_date.isnot(None),
        PerformancePip.end_date < _now().replace(microsecond=0),
    ).count()
    return {
        "total": base.count(),
        "by_status": by_status,
        "active": active,
        "successful": by_status.get(P.SUCCESSFUL.value, 0),
        "unsuccessful": by_status.get(P.UNSUCCESSFUL.value, 0),
        "overdue": due_soon,
    }


@router.get("/")
def list_pips(
    page: int = 1,
    limit: int = Query(30, ge=1, le=100),
    employee_id: Optional[UUID] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(PerformancePip).filter(PerformancePip.is_deleted == False)  # noqa: E712
    if employee_id:
        q = q.filter(PerformancePip.employee_id == employee_id)
    if status:
        q = q.filter(PerformancePip.status == status)
    total = q.count()
    rows = q.order_by(PerformancePip.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    maps = {"desig": {}, "dept": {}}
    return {"items": [serialize_pip(db, p, maps) for p in rows], "total": total, "page": page, "limit": limit}


@router.post("/", status_code=201)
def create_pip(payload: PipCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    objectives = [
        {"title": o.title, "measure": o.measure, "target": o.target, "status": o.status or "OPEN"}
        for o in payload.objectives
    ]
    manager_id = payload.manager_id or getattr(emp, "reporting_manager_id", None)
    p = PerformancePip(
        employee_id=emp.id, review_id=payload.review_id, manager_id=manager_id,
        title=payload.title, reason=payload.reason, expectations=payload.expectations,
        support=payload.support, status=P.ACTIVE.value if payload.activate else P.DRAFT.value,
        start_date=_to_dt(payload.start_date), end_date=_to_dt(payload.end_date),
        objectives_json=objectives, check_ins_json=[], created_by_id=admin.id,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return serialize_pip(db, p)


@router.get("/{pip_id}")
def get_pip(pip_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return serialize_pip(db, _load(db, pip_id))


@router.patch("/{pip_id}")
def update_pip(pip_id: UUID, payload: PipUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    p = _load(db, pip_id)
    data = payload.model_dump(exclude_unset=True)
    for k in ("title", "reason", "expectations", "support", "manager_id", "outcome"):
        if k in data:
            setattr(p, k, data[k])
    if "start_date" in data:
        p.start_date = _to_dt(data["start_date"])
    if "end_date" in data:
        p.end_date = _to_dt(data["end_date"])
    if "objectives" in data and data["objectives"] is not None:
        p.objectives_json = [
            {"title": o.get("title"), "measure": o.get("measure"), "target": o.get("target"), "status": o.get("status") or "OPEN"}
            for o in data["objectives"]
        ]
    db.commit()
    db.refresh(p)
    return serialize_pip(db, p)


@router.post("/{pip_id}/check-in")
def pip_check_in(pip_id: UUID, payload: PipCheckIn, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    p = _load(db, pip_id)
    entry = {"at": _now().isoformat(), "note": payload.note, "rating": payload.rating, "by": user_name(admin) or "HR"}
    p.check_ins_json = (p.check_ins_json or []) + [entry]
    db.commit()
    db.refresh(p)
    return serialize_pip(db, p)


@router.post("/{pip_id}/transition")
def transition(pip_id: UUID, payload: PipTransition, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    p = _load(db, pip_id)
    target = payload.to
    if target not in {s.value for s in P}:
        raise HTTPException(400, "Unknown status")
    if target not in ALLOWED.get(p.status, set()):
        raise HTTPException(409, f"Cannot move {p.status} → {target}")
    p.status = target
    if payload.outcome is not None:
        p.outcome = payload.outcome
    if target in (P.SUCCESSFUL.value, P.UNSUCCESSFUL.value) and not p.end_date:
        p.end_date = _now()
    db.commit()
    db.refresh(p)
    return serialize_pip(db, p)


@router.delete("/{pip_id}", status_code=204)
def delete_pip(pip_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    p = _load(db, pip_id)
    p.is_deleted = True
    db.commit()
