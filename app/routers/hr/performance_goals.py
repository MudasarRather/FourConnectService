"""HR Performance — Goals & OKRs (admin / superuser).

Objectives (qualitative aims) with weighted Key Results (measurable outcomes),
plus standalone goals. Progress on a KR/goal is derived from its current value
against its target; an objective rolls up the weighted mean of its KRs. Every
value change is journalled into ``check_ins_json``.

Distinct prefix /hr/performance-goals — never collides with /hr/performance/{id}.
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
from app.models.hr.performance_goal import PerformanceGoal, GoalType, GoalStatus
from app.schemas.hr.performance_goal import GoalCreate, ObjectiveCreate, GoalUpdate, GoalCheckIn
from app.utils.hr.performance_service import (
    serialize_goal, goal_progress_from_value, derive_goal_status, recompute_objective, user_name,
)

router = APIRouter(prefix="/hr/performance-goals", tags=["HR — Performance Goals"])


def _now():
    return datetime.now(timezone.utc)


def _to_dt(d: Optional[date]):
    return datetime.combine(d, datetime.min.time()) if d else None


def _load(db: Session, goal_id: UUID) -> PerformanceGoal:
    g = db.query(PerformanceGoal).filter(
        PerformanceGoal.id == goal_id, PerformanceGoal.is_deleted == False,  # noqa: E712
    ).first()
    if not g:
        raise HTTPException(404, "Goal not found")
    return g


def _recompute_self(g: PerformanceGoal):
    """Derive progress/status for a leaf goal/KR from its measured values."""
    if g.goal_type == GoalType.OBJECTIVE.value:
        return
    g.progress = goal_progress_from_value(g.start_value, g.current_value, g.target_value, g.metric_type)
    g.status = derive_goal_status(g.progress, g.due_date, g.status)


def _rollup_parent(db: Session, g: PerformanceGoal):
    if not g.parent_id:
        return
    parent = db.query(PerformanceGoal).filter(PerformanceGoal.id == g.parent_id).first()
    if not parent:
        return
    kids = db.query(PerformanceGoal).filter(
        PerformanceGoal.parent_id == parent.id, PerformanceGoal.is_deleted == False,  # noqa: E712
    ).all()
    recompute_objective(parent, kids)


@router.get("/stats")
def goal_stats(
    cycle: Optional[str] = None,
    employee_id: Optional[UUID] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    base = db.query(PerformanceGoal).filter(PerformanceGoal.is_deleted == False)  # noqa: E712
    if cycle:
        base = base.filter(PerformanceGoal.cycle == cycle)
    if employee_id:
        base = base.filter(PerformanceGoal.employee_id == employee_id)

    by_status = {}
    for st, cnt in base.with_entities(PerformanceGoal.status, func.count(PerformanceGoal.id)).group_by(PerformanceGoal.status).all():
        by_status[st] = cnt
    objectives = base.filter(PerformanceGoal.goal_type == GoalType.OBJECTIVE.value).count()
    key_results = base.filter(PerformanceGoal.goal_type == GoalType.KEY_RESULT.value).count()
    avg_progress = base.filter(
        PerformanceGoal.goal_type != GoalType.KEY_RESULT.value,
    ).with_entities(func.avg(PerformanceGoal.progress)).scalar()
    at_risk = by_status.get(GoalStatus.AT_RISK.value, 0) + by_status.get(GoalStatus.OFF_TRACK.value, 0)
    achieved = by_status.get(GoalStatus.ACHIEVED.value, 0)
    return {
        "total": base.count(),
        "objectives": objectives,
        "key_results": key_results,
        "by_status": by_status,
        "at_risk": at_risk,
        "achieved": achieved,
        "avg_progress": round(float(avg_progress), 1) if avg_progress is not None else 0,
    }


@router.get("/")
def list_goals(
    page: int = 1,
    limit: int = Query(50, ge=1, le=200),
    employee_id: Optional[UUID] = None,
    cycle: Optional[str] = None,
    status: Optional[str] = None,
    goal_type: Optional[str] = None,
    top_level: bool = False,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    q = db.query(PerformanceGoal).filter(PerformanceGoal.is_deleted == False)  # noqa: E712
    if employee_id:
        q = q.filter(PerformanceGoal.employee_id == employee_id)
    if cycle:
        q = q.filter(PerformanceGoal.cycle == cycle)
    if status:
        q = q.filter(PerformanceGoal.status == status)
    if goal_type:
        q = q.filter(PerformanceGoal.goal_type == goal_type)
    if top_level:
        q = q.filter(PerformanceGoal.parent_id.is_(None))
    total = q.count()
    rows = q.order_by(PerformanceGoal.created_at.desc()).offset((page - 1) * limit).limit(limit).all()
    maps = {"desig": {}, "dept": {}}
    with_children = top_level
    return {
        "items": [serialize_goal(db, g, maps, with_children=with_children) for g in rows],
        "total": total, "page": page, "limit": limit,
    }


@router.get("/employees/{employee_id}")
def employee_goals(
    employee_id: UUID,
    cycle: Optional[str] = None,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    """An employee's objectives (with KRs) + standalone goals for a cycle."""
    q = db.query(PerformanceGoal).filter(
        PerformanceGoal.employee_id == employee_id,
        PerformanceGoal.is_deleted == False,  # noqa: E712
        PerformanceGoal.parent_id.is_(None),
    )
    if cycle:
        q = q.filter(PerformanceGoal.cycle == cycle)
    rows = q.order_by(PerformanceGoal.created_at.desc()).all()
    maps = {"desig": {}, "dept": {}}
    return {"items": [serialize_goal(db, g, maps, with_children=True) for g in rows], "total": len(rows)}


@router.post("/", status_code=201)
def create_goal(payload: GoalCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    parent = None
    if payload.parent_id:
        parent = _load(db, payload.parent_id)
    g = PerformanceGoal(
        employee_id=emp.id,
        parent_id=payload.parent_id,
        goal_type=payload.goal_type or (GoalType.KEY_RESULT.value if payload.parent_id else GoalType.GOAL.value),
        title=payload.title,
        description=payload.description,
        category=payload.category or (parent.category if parent else None),
        cycle=payload.cycle or (parent.cycle if parent else "ANNUAL"),
        period_label=payload.period_label or (parent.period_label if parent else None),
        weight=payload.weight or 0,
        metric_type=payload.metric_type or "PERCENT",
        start_value=payload.start_value or 0,
        target_value=payload.target_value,
        current_value=payload.current_value or 0,
        unit=payload.unit,
        status=payload.status or GoalStatus.DRAFT.value,
        start_date=_to_dt(payload.start_date),
        due_date=_to_dt(payload.due_date),
        review_id=payload.review_id,
        check_ins_json=[],
        created_by_id=admin.id,
    )
    _recompute_self(g)
    db.add(g)
    db.flush()
    _rollup_parent(db, g)
    db.commit()
    db.refresh(g)
    return serialize_goal(db, g)


@router.post("/objective", status_code=201)
def create_objective(payload: ObjectiveCreate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    """Create an objective and its key results in one shot."""
    emp = db.query(Employee).filter(Employee.id == payload.employee_id, Employee.is_deleted == False).first()  # noqa: E712
    if not emp:
        raise HTTPException(404, "Employee not found")
    cycle = payload.cycle or "ANNUAL"
    obj = PerformanceGoal(
        employee_id=emp.id, goal_type=GoalType.OBJECTIVE.value, title=payload.title,
        description=payload.description, category=payload.category, cycle=cycle,
        period_label=payload.period_label, weight=payload.weight or 0,
        metric_type="PERCENT", progress=0, status=GoalStatus.ON_TRACK.value,
        start_date=_to_dt(payload.start_date), due_date=_to_dt(payload.due_date),
        review_id=payload.review_id, check_ins_json=[], created_by_id=admin.id,
    )
    db.add(obj)
    db.flush()
    for kr in payload.key_results:
        child = PerformanceGoal(
            employee_id=emp.id, parent_id=obj.id, goal_type=GoalType.KEY_RESULT.value,
            title=kr.title, category=payload.category, cycle=cycle, period_label=payload.period_label,
            weight=kr.weight or 0, metric_type=kr.metric_type or "PERCENT",
            start_value=kr.start_value or 0, target_value=kr.target_value, current_value=kr.current_value or 0,
            unit=kr.unit, status=GoalStatus.ON_TRACK.value,
            start_date=_to_dt(payload.start_date), due_date=_to_dt(payload.due_date),
            review_id=payload.review_id, check_ins_json=[], created_by_id=admin.id,
        )
        child.progress = goal_progress_from_value(child.start_value, child.current_value, child.target_value, child.metric_type)
        db.add(child)
    db.flush()
    kids = db.query(PerformanceGoal).filter(PerformanceGoal.parent_id == obj.id).all()
    recompute_objective(obj, kids)
    db.commit()
    db.refresh(obj)
    return serialize_goal(db, obj, with_children=True)


@router.get("/{goal_id}")
def get_goal(goal_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    return serialize_goal(db, _load(db, goal_id), with_children=True)


@router.patch("/{goal_id}")
def update_goal(goal_id: UUID, payload: GoalUpdate, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    g = _load(db, goal_id)
    data = payload.model_dump(exclude_unset=True)
    for k in ("title", "description", "category", "weight", "metric_type", "start_value",
              "target_value", "current_value", "unit"):
        if k in data:
            setattr(g, k, data[k])
    if "start_date" in data:
        g.start_date = _to_dt(data["start_date"])
    if "due_date" in data:
        g.due_date = _to_dt(data["due_date"])
    # explicit status/progress override only for objectives or manual closure
    if "status" in data and data["status"]:
        g.status = data["status"]
    if "progress" in data and data["progress"] is not None and g.goal_type == GoalType.OBJECTIVE.value:
        g.progress = data["progress"]
    _recompute_self(g)
    db.flush()
    _rollup_parent(db, g)
    db.commit()
    db.refresh(g)
    return serialize_goal(db, g, with_children=True)


@router.post("/{goal_id}/check-in")
def check_in(goal_id: UUID, payload: GoalCheckIn, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    g = _load(db, goal_id)
    if g.goal_type == GoalType.OBJECTIVE.value:
        raise HTTPException(409, "Check in on a key result — the objective rolls up automatically.")
    if payload.current_value is not None:
        g.current_value = payload.current_value
    _recompute_self(g)
    if payload.progress is not None and (g.metric_type or "").upper() in ("MILESTONE", "BOOLEAN"):
        g.progress = payload.progress
    if payload.status:
        g.status = payload.status
    entry = {
        "at": _now().isoformat(),
        "progress": float(g.progress or 0),
        "current_value": float(g.current_value) if g.current_value is not None else None,
        "note": payload.note or "",
        "status": g.status,
        "by": user_name(admin) or "HR",
    }
    g.check_ins_json = (g.check_ins_json or []) + [entry]
    db.flush()
    _rollup_parent(db, g)
    db.commit()
    db.refresh(g)
    return serialize_goal(db, g)


@router.delete("/{goal_id}", status_code=204)
def delete_goal(goal_id: UUID, db: Session = Depends(get_db), admin: User = Depends(get_current_superuser)):
    g = _load(db, goal_id)
    g.is_deleted = True
    # soft-delete the KRs too
    if g.goal_type == GoalType.OBJECTIVE.value:
        db.query(PerformanceGoal).filter(PerformanceGoal.parent_id == g.id).update(
            {PerformanceGoal.is_deleted: True}, synchronize_session=False,
        )
    parent_id = g.parent_id
    db.flush()
    if parent_id:
        _rollup_parent(db, g)
    db.commit()
