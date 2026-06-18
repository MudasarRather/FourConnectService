"""HR Training & Development — shared DB helpers (self-employee resolution,
request-number generation, name lookups, eligibility resolution, step enrichment).
Keeps the routers thin. Mirrors ``app/utils/hr/reimbursements/service.py``.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Dict, Any
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.system_setting import SystemSetting
from app.models.hr.employee import Employee, LifecycleState
from app.models.hr.department import Department
from app.models.hr.designation import Designation
from app.models.hr.training_request import TrainingRequest


# ─── self-employee resolution (mirrors reimbursements/leaves) ───

def resolve_self_employee(db: Session, user: User) -> Employee:
    emp = db.query(Employee).filter(
        Employee.user_id == user.id, Employee.is_deleted == False,  # noqa: E712
    ).first()
    if not emp:
        raise HTTPException(404, "Your account is not linked to an employee profile. Contact HR.")
    return emp


def try_self_employee(db: Session, user: User) -> Optional[Employee]:
    return db.query(Employee).filter(
        Employee.user_id == user.id, Employee.is_deleted == False,  # noqa: E712
    ).first()


# ─── reference number generator (mirrors reimbursements counter) ───

def generate_request_number(db: Session) -> str:
    yy = str(date.today().year)[-2:]
    key = "training_request_counter"
    for _ in range(6):
        row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
        if row:
            try:
                n = int(row.value) + 1
            except Exception:
                n = 1
            row.value = str(n)
        else:
            n = 1
            db.add(SystemSetting(key=key, value="1", description="Monotonic counter for TrainingRequest.request_number"))
        db.flush()
        candidate = f"TR-{yy}-{n:06d}"
        exists = db.query(TrainingRequest.id).filter(TrainingRequest.request_number == candidate).first()
        if not exists:
            return candidate
    raise HTTPException(500, "Could not allocate training request number")


# ─── name lookups ───

def user_name(db: Session, uid: Optional[UUID]) -> Optional[str]:
    if not uid:
        return None
    r = db.query(User.full_name).filter(User.id == uid).first()
    return r[0] if r else None


def emp_display(db: Session, eid: Optional[UUID]) -> dict:
    """Return {name, code, dept, desg} for an employee id (null-safe)."""
    if not eid:
        return {}
    r = (
        db.query(
            User.full_name.label("name"), Employee.employee_id.label("code"),
            Department.name.label("dept"), Designation.name.label("desg"),
        )
        .join(Employee, Employee.user_id == User.id)
        .outerjoin(Department, Department.id == Employee.department_id)
        .outerjoin(Designation, Designation.id == Employee.designation_id)
        .filter(Employee.id == eid)
        .first()
    )
    if not r:
        return {}
    return {"name": r.name, "code": r.code, "dept": r.dept, "desg": r.desg}


def employee_snapshot(db: Session, employee_id: UUID) -> dict:
    snap = emp_display(db, employee_id)
    if not snap:
        return {}
    mgr = db.query(Employee.reporting_manager_id).filter(Employee.id == employee_id).first()
    snap["reporting_manager_id"] = mgr[0] if mgr else None
    return snap


# ─── eligibility resolution (mirrors ClaimPolicy.eligibility resolver) ───

def resolve_eligible_employee_ids(db: Session, applies_to: Optional[Dict[str, Any]]) -> List[UUID]:
    """Resolve the active-employee set for a compliance training's ``applies_to``
    scope. Empty / null scope → all active employees."""
    q = db.query(Employee.id).filter(
        Employee.is_deleted == False,  # noqa: E712
        Employee.lifecycle_state.in_([
            LifecycleState.ACTIVE, LifecycleState.ON_PROBATION, LifecycleState.ON_NOTICE,
        ]),
    )
    scope = applies_to or {}
    dept_ids = scope.get("department_ids") or []
    desg_ids = scope.get("designation_ids") or []
    grade_ids = scope.get("grade_ids") or []
    emp_types = scope.get("employment_types") or []
    if dept_ids:
        q = q.filter(Employee.department_id.in_(dept_ids))
    if desg_ids:
        q = q.filter(Employee.designation_id.in_(desg_ids))
    if grade_ids:
        q = q.filter(Employee.grade_id.in_(grade_ids))
    if emp_types:
        q = q.filter(Employee.employment_type.in_(emp_types))
    return [r[0] for r in q.all()]


# ─── approval-step name enrichment ───

def enrich_steps_with_names(db: Session, steps: List[dict]) -> List[dict]:
    if not steps:
        return steps
    uids = set()
    for s in steps:
        for k in ("approver_user_id", "decided_by_id"):
            v = s.get(k)
            if v:
                uids.add(v)
    if not uids:
        return [dict(s) for s in steps]
    try:
        uuid_objs = [UUID(u) for u in uids]
    except Exception:
        return [dict(s) for s in steps]
    rows = db.query(User.id, User.full_name).filter(User.id.in_(uuid_objs)).all()
    name_by_id = {str(r[0]): r[1] for r in rows}
    out = []
    for s in steps:
        e = dict(s)
        if e.get("approver_user_id"):
            e["approver_name"] = name_by_id.get(e["approver_user_id"])
        if e.get("decided_by_id"):
            e["decided_by_name"] = name_by_id.get(e["decided_by_id"])
        out.append(e)
    return out
